"""
Project Aurora — Phase 6L Focused Transport Tests
Verifies the implementation of Phase 6L Architecture Specification v1.0.10:
- Exhaustive error code mapping totality and non-optimizable validation
- Deterministic 404 vs 409 error contracts
- Two-tier governed execution via POST /daily/plan/apply and POST /daily/evening-review/apply
- AST inspection ensuring zero assert invariants and zero .get() fallbacks
"""

import ast
import inspect
import pytest
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.identity.models import UserIdentity, DeviceIdentity
from app.services.agenda.horizon_models import (
    HorizonFailureReason,
    HorizonProposalStatus,
    HorizonProposalType,
    HorizonStateTransitionError,
    PreconditionFailedError,
    CANONICAL_TRANSPORT_CODE_MAP,
    _validate_transport_code_map,
    DayPlanProposal,
    EveningReviewProposal,
)
from app.services.agenda.horizon import (
    ExecutiveHorizonOrchestrationService,
    get_horizon_service_dependency,
)


USER_A = "user-alpha"
USER_B = "user-beta"


@pytest.fixture
def auth_principal_a():
    return AuthenticatedPrincipal(
        user=UserIdentity(user_id=USER_A),
        device=DeviceIdentity(device_id="dev-a", user_id=USER_A),
    )


@pytest.fixture
def auth_principal_b():
    return AuthenticatedPrincipal(
        user=UserIdentity(user_id=USER_B),
        device=DeviceIdentity(device_id="dev-b", user_id=USER_B),
    )


# ==============================================================================
# 1. Error Model & Totality Validation Unit Tests
# ==============================================================================

def test_mapping_totality_validation_succeeds_for_complete_map():
    """Confirms _validate_transport_code_map runs without error on the canonical map."""
    _validate_transport_code_map()


def test_mapping_totality_validation_detects_missing_member():
    """Confirms _validate_transport_code_map raises RuntimeError if a member is missing."""
    import app.services.agenda.horizon_models as hm
    original_map = hm.CANONICAL_TRANSPORT_CODE_MAP.copy()
    try:
        del hm.CANONICAL_TRANSPORT_CODE_MAP[HorizonFailureReason.EXPIRED]
        with pytest.raises(RuntimeError, match="Missing mappings for: \\['EXPIRED'\\]"):
            hm._validate_transport_code_map()
    finally:
        hm.CANONICAL_TRANSPORT_CODE_MAP.clear()
        hm.CANONICAL_TRANSPORT_CODE_MAP.update(original_map)


def test_mapping_totality_validation_detects_extra_member():
    """Confirms _validate_transport_code_map raises RuntimeError if an extraneous key exists."""
    import app.services.agenda.horizon_models as hm
    original_map = hm.CANONICAL_TRANSPORT_CODE_MAP.copy()
    try:
        # Create a mock extraneous key
        hm.CANONICAL_TRANSPORT_CODE_MAP["EXTRA_KEY"] = "EXTRA"
        with pytest.raises(RuntimeError, match="Extraneous mappings"):
            hm._validate_transport_code_map()
    finally:
        hm.CANONICAL_TRANSPORT_CODE_MAP.clear()
        hm.CANONICAL_TRANSPORT_CODE_MAP.update(original_map)


def test_precondition_failed_error_rejects_non_enum_code():
    """PreconditionFailedError requires code to be an instance of HorizonFailureReason."""
    with pytest.raises(TypeError, match="code must be an instance of HorizonFailureReason"):
        PreconditionFailedError(code="EXPIRED", message="Proposal has expired.")


def test_precondition_failed_error_preserves_attributes():
    """PreconditionFailedError correctly exposes code, message, and preserves exception string."""
    err = PreconditionFailedError(
        code=HorizonFailureReason.EXPIRED,
        message="Proposal has expired.",
    )
    assert err.code == HorizonFailureReason.EXPIRED
    assert err.message == "Proposal has expired."
    assert str(err) == "Proposal has expired."


# ==============================================================================
# 2. AST Structural Invariant Audits
# ==============================================================================

def test_ast_totality_validation_does_not_use_python_assert():
    """Verifies that _validate_transport_code_map is NOT implemented using Python assert."""
    import app.services.agenda.horizon_models as hm
    src = inspect.getsource(hm._validate_transport_code_map)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Assert), (
            "Found Python assert statement in _validate_transport_code_map; "
            "production invariants must raise explicit RuntimeError to survive python -O."
        )


def test_ast_agenda_endpoint_uses_direct_subscript_without_get():
    """Verifies that agenda.py uses direct subscripting CANONICAL_TRANSPORT_CODE_MAP[exc.code] and not .get()."""
    import app.api.v1.endpoints.agenda as agenda_mod
    src = inspect.getsource(agenda_mod.apply_daily_plan_endpoint)
    assert "CANONICAL_TRANSPORT_CODE_MAP.get" not in src
    assert "CANONICAL_TRANSPORT_CODE_MAP[exc.code]" in src


# ==============================================================================
# 3. Transport API Integration Tests (POST /daily/plan/apply)
# ==============================================================================

@pytest.mark.asyncio
async def test_apply_daily_plan_success(auth_principal_a):
    """Successful governed execution of DayPlanProposal returns 200 OK."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_executed_proposal = mock_proposal.model_copy()
    mock_executed_proposal.status = HorizonProposalStatus.COMPLETED

    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.return_value = mock_executed_proposal

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["proposal_id"] == "prop_day_123"
            assert data["status"] == "completed"
            mock_service.execute_proposal.assert_awaited_once_with(
                proposal_id="prop_day_123",
                user_id=USER_A,
                approved=True,
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_unknown_proposal_returns_404(auth_principal_a):
    """Unknown proposal ID raises ValueError from get_proposal, mapped to HTTP 404."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    mock_service.get_proposal.side_effect = ValueError("Proposal 'nonexistent' not found.")

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "nonexistent", "approved": True},
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Horizon proposal not found."
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_cross_user_proposal_returns_404(auth_principal_a):
    """Proposal belonging to User B accessed by User A raises ValueError from get_proposal -> 404."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    mock_service.get_proposal.side_effect = ValueError("Proposal 'cross_user' not found.")

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "cross_user", "approved": True},
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Horizon proposal not found."
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_expired_returns_409_expired(auth_principal_a):
    """Expired proposal raises PreconditionFailedError(EXPIRED), mapped to HTTP 409 {"code": "EXPIRED"}."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now - timedelta(seconds=1),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.side_effect = PreconditionFailedError(
        code=HorizonFailureReason.EXPIRED,
        message="Proposal has expired.",
    )

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["code"] == "EXPIRED"
            assert detail["message"] == "Proposal has expired."
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_state_hash_mismatch_returns_409(auth_principal_a):
    """State hash mismatch raises PreconditionFailedError(STATE_CHANGED) -> 409 STATE_HASH_MISMATCH."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_old",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.side_effect = PreconditionFailedError(
        code=HorizonFailureReason.STATE_CHANGED,
        message="STALE_STATE_HASH",
    )

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["code"] == "STATE_HASH_MISMATCH"
            assert detail["message"] == "STALE_STATE_HASH"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_stale_calendar_returns_409(auth_principal_a):
    """Calendar TOCTOU collision raises PreconditionFailedError(STALE_CALENDAR_STATE) -> 409."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.side_effect = PreconditionFailedError(
        code=HorizonFailureReason.STALE_CALENDAR_STATE,
        message="STALE_CALENDAR_STATE",
    )

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["code"] == "STALE_CALENDAR_STATE"
            assert detail["message"] == "STALE_CALENDAR_STATE"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_scheduled_time_in_past_returns_409(auth_principal_a):
    """Operation timestamp in past raises PreconditionFailedError(SCHEDULED_TIME_IN_PAST) -> 409."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.side_effect = PreconditionFailedError(
        code=HorizonFailureReason.SCHEDULED_TIME_IN_PAST,
        message="SCHEDULED_TIME_IN_PAST",
    )

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["code"] == "PRECONDITION_FAILED"
            assert detail["message"] == "SCHEDULED_TIME_IN_PAST"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_illegal_state_transition_returns_409(auth_principal_a):
    """Illegal state transition raises HorizonStateTransitionError -> 409 ILLEGAL_STATE_TRANSITION."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.COMPLETED,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.side_effect = HorizonStateTransitionError(
        "Illegal horizon proposal state transition from completed to executing"
    )

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["code"] == "ILLEGAL_STATE_TRANSITION"
            assert "Illegal horizon proposal state transition" in detail["message"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_permission_denial_returns_403(auth_principal_a):
    """Permission denial raises PermissionError -> 403 Forbidden."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.side_effect = PermissionError("TASKS permission is required for task rollover.")

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 403
            assert "TASKS permission is required" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_concrete_type_mismatch_returns_422(auth_principal_a):
    """Submitting an EveningReviewProposal ID to /daily/plan/apply returns 422 with zero mutations."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    # Return EveningReviewProposal instead of DayPlanProposal
    mock_proposal = EveningReviewProposal(
        proposal_id="prop_eve_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_eve_123", "approved": True},
            )
            assert resp.status_code == 422
            assert "is not a DayPlanProposal" in resp.json()["detail"]
            mock_service.execute_proposal.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_missing_approved_returns_422(auth_principal_a):
    """Omission of approved field triggers Pydantic 422 Unprocessable Entity."""
    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123"},  # Missing approved
            )
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_forbidden_identity_override_returns_400(auth_principal_a):
    """X-User-ID header or user_id query param is rejected with 400 Bad Request."""
    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp_header = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                headers={"X-User-ID": "spoofed_user"},
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp_header.status_code == 400
            assert "X-User-ID header is strictly forbidden" in resp_header.json()["detail"]

            resp_query = await ac.post(
                "/api/v1/agenda/daily/plan/apply?user_id=spoofed_user",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp_query.status_code == 400
            assert "Query parameter user_id is strictly forbidden" in resp_query.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_daily_plan_unexpected_exception_returns_500(auth_principal_a):
    """Unexpected exception logs error and returns generic HTTP 500 without leaking internals."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_123",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.side_effect = RuntimeError("Fatal hardware memory fault")

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/plan/apply",
                json={"proposal_id": "prop_day_123", "approved": True},
            )
            assert resp.status_code == 500
            assert resp.json()["detail"] == "Internal server error"
            assert "Fatal hardware memory fault" not in resp.text
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# 4. Transport API Integration Tests (POST /daily/evening-review/apply)
# ==============================================================================

@pytest.mark.asyncio
async def test_apply_evening_review_success(auth_principal_a):
    """Successful governed execution of EveningReviewProposal returns 200 OK."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = EveningReviewProposal(
        proposal_id="prop_eve_456",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_executed_proposal = mock_proposal.model_copy()
    mock_executed_proposal.status = HorizonProposalStatus.COMPLETED

    mock_service.get_proposal.return_value = mock_proposal
    mock_service.execute_proposal.return_value = mock_executed_proposal

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/evening-review/apply",
                json={"proposal_id": "prop_eve_456", "approved": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["proposal_id"] == "prop_eve_456"
            assert data["status"] == "completed"
            mock_service.execute_proposal.assert_awaited_once_with(
                proposal_id="prop_eve_456",
                user_id=USER_A,
                approved=True,
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_apply_evening_review_concrete_type_mismatch_returns_422(auth_principal_a):
    """Submitting DayPlanProposal ID to /daily/evening-review/apply returns 422 with zero mutations."""
    mock_service = AsyncMock(spec=ExecutiveHorizonOrchestrationService)
    now = datetime.now(timezone.utc)
    mock_proposal = DayPlanProposal(
        proposal_id="prop_day_789",
        user_id=USER_A,
        status=HorizonProposalStatus.DRAFT,
        expires_at=now + timedelta(minutes=30),
        state_hash="hash_abc",
        target_date=now.date(),
    )
    mock_service.get_proposal.return_value = mock_proposal

    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_horizon_service_dependency] = lambda: mock_service

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/agenda/daily/evening-review/apply",
                json={"proposal_id": "prop_day_789", "approved": True},
            )
            assert resp.status_code == 422
            assert "is not an EveningReviewProposal" in resp.json()["detail"]
            mock_service.execute_proposal.assert_not_called()
    finally:
        app.dependency_overrides.clear()
