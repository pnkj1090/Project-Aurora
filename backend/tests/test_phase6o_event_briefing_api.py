"""
Project Aurora — Phase 6O Event Briefing & Post-Event Debrief API Tests
Covers Architecture Specification v1.0.2:
1. GET briefing -> 200
2. POST debrief/propose -> 200 DRAFT
3. GET proposal -> 200 and tenant isolation (cross-user -> 404)
4. POST abort -> ABORTED
5. POST apply approved=true -> successful governed execution (COMPLETED)
6. POST apply approved=false -> ABORTED
7. cross-user apply -> 404
8. expired apply -> 409
9. state-hash drift -> 409
10. missing permission -> 403
11. partial operation failure -> PARTIALLY_EXECUTED
12. terminal proposal replay -> 409
13. malformed ApplyDebriefRequest is rejected (422)
14. extra request fields are forbidden (422)
15. authenticated principal is only authority, identity overrides rejected (400)
16. structured error contract is preserved
"""

import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.identity.models import UserIdentity, DeviceIdentity
from app.services.calendar.models import CalendarEvent
from app.services.calendar.manager import CalendarManager
from app.services.tasks import Task, TaskStatus, TaskPriority, TaskManager, InMemoryTaskStore
from app.services.commitments import (
    Commitment,
    CommitmentStatus,
    CommitmentPriority,
    CommitmentManager,
    InMemoryCommitmentStore,
)
from app.services.reminders import Reminder, ReminderManager, InMemoryReminderStore
from app.services.memory.schemas import MemoryItem, MemoryType, MemorySource, MemoryStatus
from app.services.memory.manager import MemoryManager
from app.services.memory.store import InMemoryMemoryStore
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore

from app.services.agenda.briefing_models import (
    DebriefProposalStatus,
    DebriefFailureReason,
    ReminderLinkType,
    DebriefOperationType,
    DebriefOperationStatus,
    DebriefStateTransitionError,
    TaskCreateExecutionPrecondition,
    ReminderCreateExecutionPrecondition,
    MemoryCreateExecutionPrecondition,
    DebriefOperationPrecondition,
    DebriefOperation,
    DebriefProposal,
    EventBriefing,
    DEBRIEF_PROPOSAL_TTL_MINUTES,
    validate_debrief_state_transition,
    compute_debrief_state_hash,
)
from app.services.agenda.briefing import EventBriefingService, get_briefing_service_dependency


USER_A = "user-alice-6o"
USER_B = "user-bob-6o"


@pytest.fixture
def auth_principal_a():
    return AuthenticatedPrincipal(
        user=UserIdentity(user_id=USER_A),
        device=DeviceIdentity(device_id="dev-a-6o", user_id=USER_A),
    )


@pytest.fixture
def auth_principal_b():
    return AuthenticatedPrincipal(
        user=UserIdentity(user_id=USER_B),
        device=DeviceIdentity(device_id="dev-b-6o", user_id=USER_B),
    )


class BriefingTestRig:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.perm_store = InMemoryPermissionStore()
        self.perm_manager = PermissionManager(store=self.perm_store)

        self.cal_manager = CalendarManager(permission_manager=self.perm_manager)
        self.task_store = InMemoryTaskStore()
        self.task_manager = TaskManager(store=self.task_store)
        self.cmt_store = InMemoryCommitmentStore()
        self.cmt_manager = CommitmentManager(store=self.cmt_store)
        self.rem_store = InMemoryReminderStore()
        self.rem_manager = ReminderManager(
            store=self.rem_store,
            task_manager=self.task_manager,
            commitment_manager=self.cmt_manager,
        )
        self.mem_store = InMemoryMemoryStore()
        self.mem_manager = MemoryManager(store=self.mem_store)

        self.service = EventBriefingService(
            calendar_manager=self.cal_manager,
            task_manager=self.task_manager,
            commitment_manager=self.cmt_manager,
            reminder_manager=self.rem_manager,
            memory_manager=self.mem_manager,
            permission_manager=self.perm_manager,
        )

    async def init_permissions(self):
        for pt in [PermissionType.CALENDAR, PermissionType.TASKS, PermissionType.REMINDERS]:
            await self.perm_manager.update_permission(pt, PermissionStatus.AUTHORIZED, user_id=self.user_id)

    async def seed_event(self, event_id: str = "evt_briefing_test") -> CalendarEvent:
        now = datetime.now(timezone.utc)
        ev = CalendarEvent(
            event_id=event_id,
            user_id=self.user_id,
            title="Design Review Sync",
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            location="Conference Room A",
            attendees=["alice@example.com", "bob@example.com"],
        )
        await self.cal_manager.set_events([ev], user_id=self.user_id)
        return ev


@pytest_asyncio.fixture
async def test_rig_a():
    rig = BriefingTestRig(USER_A)
    await rig.init_permissions()
    await rig.seed_event()
    return rig


@pytest_asyncio.fixture
async def client_a(test_rig_a, auth_principal_a):
    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    app.dependency_overrides[get_briefing_service_dependency] = lambda: test_rig_a.service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ==============================================================================
# 1. GET briefing -> 200
# ==============================================================================
@pytest.mark.asyncio
async def test_get_event_briefing_200(client_a):
    response = await client_a.get("/api/v1/agenda/events/evt_briefing_test/briefing")
    assert response.status_code == 200
    data = response.json()
    assert data["event_id"] == "evt_briefing_test"
    assert data["title"] == "Design Review Sync"
    assert "attendees" in data
    assert "summary" in data


# ==============================================================================
# 2. POST debrief/propose -> 200 DRAFT
# ==============================================================================
@pytest.mark.asyncio
async def test_propose_event_debrief_200_draft(client_a):
    payload = {
        "raw_notes": "We agreed to ship Phase 6O tomorrow.",
        "task_candidates": [
            {
                "exact_title": "Finalize Phase 6O client models",
                "exact_priority": "high",
                "exact_due_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            }
        ],
        "reminder_candidates": [
            {
                "exact_title": "Check CI build",
                "exact_scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
                "linked_entity_type": "none",
            }
        ],
        "memory_candidates": [
            {
                "exact_content": "Shipped Phase 6O on schedule",
                "exact_memory_type": "episodic",
                "exact_confidence": 0.95,
            }
        ],
    }
    response = await client_a.post("/api/v1/agenda/events/evt_briefing_test/debrief/propose", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "draft"
    assert data["event_id"] == "evt_briefing_test"
    assert len(data["ordered_operations"]) == 3
    assert data["proposal_id"].startswith("dbp_")


# ==============================================================================
# 3. GET proposal -> 200 and tenant isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_get_proposal_and_tenant_isolation(client_a, auth_principal_a, auth_principal_b):
    # User A proposes
    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Important sync notes"},
    )
    assert prop_resp.status_code == 200
    proposal_id = prop_resp.json()["proposal_id"]

    # User A gets the proposal -> 200
    get_resp = await client_a.get(f"/api/v1/agenda/debrief/proposals/{proposal_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["proposal_id"] == proposal_id

    # Switch identity to User B: attempts to access User A's proposal -> 404 (tenant isolation)
    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_b
    cross_resp = await client_a.get(f"/api/v1/agenda/debrief/proposals/{proposal_id}")
    assert cross_resp.status_code == 404


# ==============================================================================
# 4. POST abort -> ABORTED
# ==============================================================================
@pytest.mark.asyncio
async def test_abort_debrief_proposal(client_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Meeting aborted notes"},
    )
    proposal_id = prop_resp.json()["proposal_id"]

    abort_resp = await client_a.post(f"/api/v1/agenda/debrief/proposals/{proposal_id}/abort")
    assert abort_resp.status_code == 200
    data = abort_resp.json()
    assert data["status"] == "aborted"


# ==============================================================================
# 5. POST apply approved=true -> successful governed execution
# ==============================================================================
@pytest.mark.asyncio
async def test_apply_debrief_proposal_approved_true(client_a, test_rig_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={
            "raw_notes": "Followup task",
            "task_candidates": [
                {
                    "exact_title": "Implement Client Models",
                    "exact_priority": "high",
                    "exact_due_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                }
            ],
        },
    )
    proposal_id = prop_resp.json()["proposal_id"]

    apply_resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True},
    )
    assert apply_resp.status_code == 200
    data = apply_resp.json()
    assert data["status"] == "completed"
    assert data["ordered_operations"][0]["status"] == "completed"
    assert data["ordered_operations"][0]["created_entity_id"] is not None

    # Verify task was created in manager
    task_id = data["ordered_operations"][0]["created_entity_id"]
    created_task = await test_rig_a.task_manager.get_task(task_id=task_id, user_id=USER_A)
    assert created_task is not None
    assert created_task.title == "Implement Client Models"


# ==============================================================================
# 6. POST apply approved=false -> ABORTED
# ==============================================================================
@pytest.mark.asyncio
async def test_apply_debrief_proposal_approved_false(client_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "To be rejected"},
    )
    proposal_id = prop_resp.json()["proposal_id"]

    apply_resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": False},
    )
    assert apply_resp.status_code == 200
    data = apply_resp.json()
    assert data["status"] == "aborted"


# ==============================================================================
# 7. Cross-user apply -> 404
# ==============================================================================
@pytest.mark.asyncio
async def test_cross_user_apply_returns_404(client_a, auth_principal_a, auth_principal_b):
    # Propose with Alice
    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_a
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Alice's notes"},
    )
    assert prop_resp.status_code == 200
    proposal_id = prop_resp.json()["proposal_id"]

    # Switch to Bob: attempts to apply Alice's proposal -> 404
    app.dependency_overrides[get_authenticated_principal] = lambda: auth_principal_b
    apply_resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True},
    )
    assert apply_resp.status_code == 404
    detail = apply_resp.json()["detail"]
    assert detail["code"] == "PROPOSAL_NOT_FOUND"


# ==============================================================================
# 8. Expired apply -> 409
# ==============================================================================
@pytest.mark.asyncio
async def test_expired_apply_returns_409(client_a, test_rig_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Old meeting notes"},
    )
    proposal_id = prop_resp.json()["proposal_id"]

    # Artificially expire the proposal
    stored_prop = test_rig_a.service._proposals[proposal_id]
    stored_prop.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    apply_resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True},
    )
    assert apply_resp.status_code == 409
    detail = apply_resp.json()["detail"]
    assert detail["code"] == "PROPOSAL_EXPIRED"


# ==============================================================================
# 9. State-hash drift -> 409
# ==============================================================================
@pytest.mark.asyncio
async def test_state_hash_drift_returns_409(client_a, test_rig_a):
    # Create an initial task that reminder will link to
    t = await test_rig_a.task_manager.create_task(title="Parent Task", user_id=USER_A)

    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={
            "raw_notes": "Notes with linked reminder",
            "reminder_candidates": [
                {
                    "exact_title": "Review Parent Task",
                    "exact_scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                    "linked_entity_type": "task",
                    "linked_entity_id": t.id,
                }
            ],
        },
    )
    proposal_id = prop_resp.json()["proposal_id"]

    # Concurrently complete the parent task (state hash drift)
    await test_rig_a.task_manager.complete_task_with_status(task_id=t.id, user_id=USER_A)

    apply_resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True},
    )
    assert apply_resp.status_code == 409
    detail = apply_resp.json()["detail"]
    assert detail["code"] == "STATE_CHANGED"


# ==============================================================================
# 10. Missing permission -> 403
# ==============================================================================
@pytest.mark.asyncio
async def test_missing_permission_returns_403(client_a, test_rig_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={
            "raw_notes": "Permission test notes",
            "task_candidates": [
                {
                    "exact_title": "Needs task permission",
                    "exact_priority": "high",
                }
            ],
        },
    )
    assert prop_resp.status_code == 200
    proposal_id = prop_resp.json()["proposal_id"]

    # Revoke TASKS permission
    await test_rig_a.perm_manager.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=USER_A)

    apply_resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True},
    )
    assert apply_resp.status_code == 403
    detail = apply_resp.json()["detail"]
    assert detail["code"] == "INSUFFICIENT_PERMISSIONS"


# ==============================================================================
# 11. Partial operation failure -> PARTIALLY_EXECUTED
# ==============================================================================
@pytest.mark.asyncio
async def test_partial_operation_failure_returns_partially_executed(client_a, test_rig_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={
            "raw_notes": "Multi op notes",
            "task_candidates": [
                {"exact_title": "Op 1 Task", "exact_priority": "high"},
            ],
            "reminder_candidates": [
                {
                    "exact_title": "Op 2 Reminder",
                    "exact_scheduled_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    "linked_entity_type": "none",
                }
            ],
        },
    )
    proposal_id = prop_resp.json()["proposal_id"]

    # Mock reminder_manager.create_reminder to fail
    with patch.object(test_rig_a.rem_manager, "create_reminder", side_effect=RuntimeError("Reminder service crashed")):
        apply_resp = await client_a.post(
            f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
            json={"approved": True},
        )
        assert apply_resp.status_code == 200
        data = apply_resp.json()
        assert data["status"] == "partially_executed"
        assert data["failure_reason"] == "OPERATION_FAILED"
        assert data["ordered_operations"][0]["status"] == "completed"
        assert data["ordered_operations"][1]["status"] == "failed"


# ==============================================================================
# 12. Terminal proposal replay -> 409
# ==============================================================================
@pytest.mark.asyncio
async def test_terminal_proposal_replay_returns_409(client_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Replay test"},
    )
    proposal_id = prop_resp.json()["proposal_id"]

    # First apply -> COMPLETED
    first_apply = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True},
    )
    assert first_apply.status_code == 200
    assert first_apply.json()["status"] == "completed"

    # Second apply on terminal proposal -> 409
    second_apply = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True},
    )
    assert second_apply.status_code == 409
    detail = second_apply.json()["detail"]
    assert detail["code"] == "ILLEGAL_STATE_TRANSITION"


# ==============================================================================
# 13. Malformed ApplyDebriefRequest is rejected (422)
# ==============================================================================
@pytest.mark.asyncio
async def test_malformed_apply_request_rejected_422(client_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Malformed test"},
    )
    proposal_id = prop_resp.json()["proposal_id"]

    resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": "not-a-boolean"},
    )
    assert resp.status_code == 422


# ==============================================================================
# 14. Extra request fields are forbidden (422)
# ==============================================================================
@pytest.mark.asyncio
async def test_extra_request_fields_forbidden_422(client_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Extra fields test"},
    )
    proposal_id = prop_resp.json()["proposal_id"]

    resp = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        json={"approved": True, "extra_unexpected_field": "disallowed"},
    )
    assert resp.status_code == 422


# ==============================================================================
# 15. Authenticated principal is only authority, identity overrides rejected
# ==============================================================================
@pytest.mark.asyncio
async def test_identity_override_headers_rejected_400(client_a):
    prop_resp = await client_a.post(
        "/api/v1/agenda/events/evt_briefing_test/debrief/propose",
        json={"raw_notes": "Identity test"},
    )
    proposal_id = prop_resp.json()["proposal_id"]

    # Reject X-User-ID header
    resp_header = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply",
        headers={"x-user-id": "attacker"},
        json={"approved": True},
    )
    assert resp_header.status_code == 400

    # Reject query parameter user_id
    resp_query = await client_a.post(
        f"/api/v1/agenda/debrief/proposals/{proposal_id}/apply?user_id=attacker",
        json={"approved": True},
    )
    assert resp_query.status_code == 400
