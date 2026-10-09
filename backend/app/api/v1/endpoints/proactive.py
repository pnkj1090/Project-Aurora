"""
Phase 5H — REST API endpoints for Proactive Assistance proposals.

Endpoints:
  GET    /api/v1/proactive/candidates              — Bounded, user-scoped list of proactive candidates
  GET    /api/v1/proactive/candidates/{candidate_id} — Retrieve a specific candidate
  POST   /api/v1/proactive/candidates/{candidate_id}/dismiss — Dismiss a candidate proposal
  POST   /api/v1/proactive/candidates/{candidate_id}/cancel — Cancel a candidate proposal

Invariants:
- Pagination: default limit=20, min=1, max=50. Strict validation (limit > 50 or < 1 -> 422).
- Zero exposure of internal metadata or reference_id.
- User isolation: active Aurora identity determines ownership. Cross-user access returns 404.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status, Body
from pydantic import BaseModel, Field

from app.services.identity import DEFAULT_LOCAL_USER_ID, default_identity_manager
from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.proactive import (
    ProactiveManager,
    ProactiveCandidate,
    ProactiveStatus,
    get_proactive_manager_dependency,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
)
from app.services.proactive.schemas import (
    AcceptProactiveRequest,
    ProactiveResolutionResponse,
    NavigationTarget,
    NavigationTargetType,
    ProactivePendingConfirmationOut,
)
from app.services.proactive.executor import (
    ProactiveActionExecutor,
    get_proactive_action_executor,
)

logger = logging.getLogger("aurora.api.proactive")
router = APIRouter()


# ── Response Models (Safe, data-minimized) ────────────────────────────────────


class ProactiveCandidateResponse(BaseModel):
    """
    Safe public representation of a ProactiveCandidate.
    CRITICAL: metadata and reference_id are strictly excluded.
    """

    candidate_id: str
    trigger: str
    title: str
    message: str
    priority: str
    status: str
    created_at: str
    expires_at: str
    source: str
    action: str

    @classmethod
    def from_domain(cls, c: ProactiveCandidate) -> "ProactiveCandidateResponse":
        return cls(
            candidate_id=c.candidate_id,
            trigger=c.trigger.value,
            title=c.title,
            message=c.message,
            priority=c.priority.value,
            status=c.status.value,
            created_at=c.created_at.isoformat(),
            expires_at=c.expires_at.isoformat(),
            source=c.source,
            action=c.action.value,
        )


class ProactiveCandidateListResponse(BaseModel):
    """Safe, paginated list of proactive proposals."""

    status: str = "success"
    count: int
    total: int
    limit: int
    offset: int
    candidates: List[ProactiveCandidateResponse]


# ── Shared Identity Dependency ────────────────────────────────────────────────


def get_current_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> str:
    """Derives target identity from header or active Aurora identity."""
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()
    return default_identity_manager.get_current_user().user_id


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/candidates",
    response_model=ProactiveCandidateListResponse,
    summary="List proactive proposals for the active user.",
)
async def list_candidates(
    limit: int = Query(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description="Maximum candidates to return (strict validation: 1 to 50).",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Pagination offset (must be non-negative).",
    ),
    candidate_status: Optional[ProactiveStatus] = Query(
        default=None,
        alias="status",
        description="Optional status filter (e.g. eligible, dismissed).",
    ),
    user_id: str = Depends(get_current_user_id),
    manager: ProactiveManager = Depends(get_proactive_manager_dependency),
):
    """
    Retrieve a bounded, user-scoped list of proactive candidates.
    Ordered deterministically by priority descending, created_at descending, candidate_id ascending.
    """
    candidates, total = await manager.list_candidates(
        user_id=user_id,
        status=candidate_status,
        limit=limit,
        offset=offset,
        include_expired=False,
    )

    items = [ProactiveCandidateResponse.from_domain(c) for c in candidates]

    return ProactiveCandidateListResponse(
        count=len(items),
        total=total,
        limit=limit,
        offset=offset,
        candidates=items,
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=ProactiveCandidateResponse,
    summary="Retrieve a proactive proposal by ID.",
)
async def get_candidate(
    candidate_id: str,
    user_id: str = Depends(get_current_user_id),
    manager: ProactiveManager = Depends(get_proactive_manager_dependency),
):
    """
    Retrieve a specific candidate proposal by ID.
    Returns 404 if not found or cross-user.
    """
    candidate = await manager.get_candidate(candidate_id=candidate_id, user_id=user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proactive candidate '{candidate_id}' not found or not accessible.",
        )
    return ProactiveCandidateResponse.from_domain(candidate)


@router.post(
    "/candidates/{candidate_id}/dismiss",
    response_model=ProactiveCandidateResponse,
    summary="Dismiss a proactive proposal.",
)
async def dismiss_candidate(
    candidate_id: str,
    user_id: str = Depends(get_current_user_id),
    manager: ProactiveManager = Depends(get_proactive_manager_dependency),
):
    """
    Explicitly dismiss a proactive proposal.
    Transitions status to 'dismissed'.
    """
    candidate = await manager.dismiss_candidate(candidate_id=candidate_id, user_id=user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proactive candidate '{candidate_id}' not found or not accessible.",
        )
    return ProactiveCandidateResponse.from_domain(candidate)


@router.post(
    "/candidates/{candidate_id}/cancel",
    response_model=ProactiveCandidateResponse,
    summary="Cancel a proactive proposal.",
)
async def cancel_candidate(
    candidate_id: str,
    user_id: str = Depends(get_current_user_id),
    manager: ProactiveManager = Depends(get_proactive_manager_dependency),
):
    """
    Cancel a proactive proposal.
    Transitions status to 'cancelled'.
    """
    candidate = await manager.cancel_candidate(candidate_id=candidate_id, user_id=user_id)
    if candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proactive candidate '{candidate_id}' not found or not accessible.",
        )
    return ProactiveCandidateResponse.from_domain(candidate)


@router.post(
    "/evaluate",
    response_model=List[ProactiveCandidateResponse],
    summary="Trigger on-demand domain proposal evaluation.",
)
async def evaluate_proposals(
    user_id: str = Depends(get_current_user_id),
    manager: ProactiveManager = Depends(get_proactive_manager_dependency),
):
    """
    On-demand domain evaluation triggered by explicit user request (e.g. pull-to-refresh).
    Inspects Tasks, Commitments, Calendar, and Reminders state and passes each candidate
    through the Phase 5H policy pipeline (dedup, 5/hr rate limit, permissions).
    Returns only newly evaluated public-safe candidates.
    Does NOT execute any action and does NOT spawn background tasks.
    """
    candidates = await manager.run_subsystem_evaluation(user_id=user_id)
    return [ProactiveCandidateResponse.from_domain(c) for c in candidates]


@router.post(
    "/candidates/{candidate_id}/accept",
    response_model=ProactiveResolutionResponse,
    summary="Accept and resolve an eligible proactive proposal.",
)
async def accept_candidate(
    candidate_id: str,
    request: AcceptProactiveRequest = Body(default_factory=AcceptProactiveRequest),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    executor: ProactiveActionExecutor = Depends(get_proactive_action_executor),
):
    """
    Explicit user acceptance of a proactive proposal.
    Initiates execution resolution:
    - Enforces TOCTOU runtime permission checks.
    - Transitions candidate ELIGIBLE -> ACCEPTED.
    - If confirmation is required: registers the existing server-held PendingConfirmation and returns safe correlation details.
    - If read-only OPEN_*: returns safe NavigationTarget.
    - If state-changing COMPLETE_*: executes only after the existing confirmation is approved.
    - Never exposes reference_id, metadata, action_payload, or internal execution details.
    """
    response = await executor.resolve_candidate_action(
        candidate_id=candidate_id,
        user_id=principal.user.user_id,
        conversation_id=request.conversation_id,
    )
    if response.status == "failed" and response.error_message == "Candidate proposal not found.":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proactive candidate '{candidate_id}' not found or not accessible.",
        )
    return response
