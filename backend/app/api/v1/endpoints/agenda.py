from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query, Request, Body, status
from pydantic import BaseModel, Field, ConfigDict

from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.agenda.horizon_models import (
    CANONICAL_TRANSPORT_CODE_MAP,
    HorizonStateTransitionError,
    PreconditionFailedError,
)
from app.services.agenda import (
    AgendaService,
    get_agenda_service_dependency,
    DailyAgendaResponse,
    ProposedSlotResponse,
    ConflictScanResponse,
    InvalidTimezoneError,
    ScheduleCoordinationService,
    get_coordination_service_dependency,
    CoordinateScheduleRequest,
    CoordinateScheduleResponse,
    ResolveConflictsRequest,
    ResolveConflictsResponse,
    EventBriefingService,
    get_briefing_service_dependency,
    EventBriefing,
    DebriefProposal,
    DebriefStateTransitionError,
    ExecutiveHorizonOrchestrationService,
    get_horizon_service_dependency,
    DailyExecutivePlan,
    EveningHorizonReview,
    DayPlanProposal,
    EveningReviewProposal,
    ExecutiveWeeklyHorizonService,
    get_weekly_horizon_service_dependency,
    WeeklyStrategicHorizon,
    WeeklyPlanProposal,
    WeeklyRetrospective,
    WeeklyRetrospectiveProposal,
    WeeklyPreconditionFailedError,
    WeeklyStateTransitionError,
)


logger = logging.getLogger("aurora.api.agenda")
router = APIRouter()


def _check_forbidden_identity_overrides(request: Request) -> None:
    """Rejects client-supplied X-User-ID header or query user_id with HTTP 400 Bad Request."""
    if "x-user-id" in request.headers:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-User-ID header is strictly forbidden; Bearer authentication is authoritative.")
    if "user_id" in request.query_params:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Query parameter user_id is strictly forbidden; Bearer authentication is authoritative.")


class ProposeSlotRequest(BaseModel):
    task_id: str = Field(..., min_length=1, description="Public task ID (e.g. tsk_123)")
    duration_minutes: int = Field(..., ge=15, description="Work duration in minutes (>= 15)")
    date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Local date YYYY-MM-DD")
    timezone: Optional[str] = Field(None, min_length=2, max_length=50, description="IANA timezone identifier")
    work_start_hour: int = Field(9, ge=0, le=23, description="Start of working hours (0-23)")
    work_end_hour: int = Field(18, ge=0, le=23, description="End of working hours (0-23)")


@router.get(
    "/daily",
    response_model=DailyAgendaResponse,
    summary="Get Daily Agenda",
    description="Retrieves the unified chronological agenda for a local date across Calendar, Tasks, Commitments, and Reminders.",
)
async def get_daily_agenda_endpoint(
    date_str: Optional[str] = Query(None, alias="date", pattern=r"^\d{4}-\d{2}-\d{2}$", description="Local date YYYY-MM-DD"),
    timezone_str: Optional[str] = Query(None, alias="timezone", min_length=2, max_length=50, description="IANA timezone"),
    work_start_hour: int = Query(9, ge=0, le=23, description="Start of working hours (0-23)"),
    work_end_hour: int = Query(18, ge=0, le=23, description="End of working hours (0-23)"),
    include_conflicts: bool = Query(True, description="Evaluate cross-domain conflicts"),
    include_free_slots: bool = Query(True, description="Calculate available working gaps"),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    agenda_service: AgendaService = Depends(get_agenda_service_dependency),
) -> DailyAgendaResponse:
    user_id = principal.user.user_id

    if work_start_hour >= work_end_hour:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="work_start_hour must be strictly less than work_end_hour.",
        )

    target_date: Optional[date] = None
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid date format '{date_str}'. Expected YYYY-MM-DD.",
            )

    try:
        return await agenda_service.get_daily_agenda(
            user_id=user_id,
            target_date=target_date,
            explicit_tz=timezone_str,
            include_conflicts=include_conflicts,
            include_free_slots=include_free_slots,
            work_start_hour=work_start_hour,
            work_end_hour=work_end_hour,
        )
    except InvalidTimezoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


@router.get(
    "/conflicts",
    response_model=ConflictScanResponse,
    summary="Detect Schedule Conflicts",
    description="Scans a lookahead window for cross-domain collisions, meeting overlaps, and deadline clashes.",
)
async def detect_conflicts_endpoint(
    window_hours: int = Query(24, ge=1, le=168, description="Lookahead window in hours (1-168)"),
    start_datetime: Optional[datetime] = Query(None, description="Window start in UTC (defaults to now)"),
    timezone_str: Optional[str] = Query(None, alias="timezone", min_length=2, max_length=50, description="IANA timezone"),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    agenda_service: AgendaService = Depends(get_agenda_service_dependency),
) -> ConflictScanResponse:
    user_id = principal.user.user_id

    try:
        return await agenda_service.detect_conflicts(
            user_id=user_id,
            window_hours=window_hours,
            start_datetime=start_datetime,
            explicit_tz=timezone_str,
        )
    except InvalidTimezoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


@router.post(
    "/propose-slot",
    response_model=ProposedSlotResponse,
    summary="Propose Feasible Work Slot",
    description="Proposes deterministic feasible free slots for a task based on calendar availability and deadline constraints.",
)
async def propose_slot_endpoint(
    payload: ProposeSlotRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    agenda_service: AgendaService = Depends(get_agenda_service_dependency),
) -> ProposedSlotResponse:
    user_id = principal.user.user_id

    if payload.work_start_hour >= payload.work_end_hour:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="work_start_hour must be strictly less than work_end_hour.",
        )

    try:
        return await agenda_service.propose_schedule_slot(
            user_id=user_id,
            task_id=payload.task_id,
            duration_minutes=payload.duration_minutes,
            date_str=payload.date,
            explicit_tz=payload.timezone,
            work_start_hour=payload.work_start_hour,
            work_end_hour=payload.work_end_hour,
        )
    except InvalidTimezoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


@router.post(
    "/coordinate",
    response_model=CoordinateScheduleResponse,
    summary="Coordinate Daily Schedule",
    description="Sequences and allocates pending tasks into available working gaps on a target date. Read-only planning analysis; does not modify task deadlines.",
)
async def coordinate_schedule_endpoint(
    payload: CoordinateScheduleRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordination_service: ScheduleCoordinationService = Depends(get_coordination_service_dependency),
) -> CoordinateScheduleResponse:
    user_id = principal.user.user_id

    if payload.work_start_hour >= payload.work_end_hour:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="work_start_hour must be strictly less than work_end_hour.",
        )

    target_date: Optional[date] = None
    if payload.target_date:
        try:
            target_date = date.fromisoformat(payload.target_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid date format '{payload.target_date}'. Expected YYYY-MM-DD.",
            )

    try:
        return await coordination_service.coordinate_schedule(
            user_id=user_id,
            target_date=target_date,
            explicit_tz=payload.timezone,
            work_start_hour=payload.work_start_hour,
            work_end_hour=payload.work_end_hour,
            default_duration_minutes=payload.default_duration_minutes,
            task_ids=payload.task_ids,
        )
    except InvalidTimezoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


@router.post(
    "/resolve-conflicts",
    response_model=ResolveConflictsResponse,
    summary="Resolve Schedule Conflicts",
    description="Scans for schedule conflicts and generates structured, non-executable resolution and mitigation strategies. Read-only analysis.",
)
async def resolve_conflicts_endpoint(
    payload: ResolveConflictsRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordination_service: ScheduleCoordinationService = Depends(get_coordination_service_dependency),
) -> ResolveConflictsResponse:
    user_id = principal.user.user_id

    if payload.work_start_hour >= payload.work_end_hour:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="work_start_hour must be strictly less than work_end_hour.",
        )

    target_date: Optional[date] = None
    if payload.target_date:
        try:
            target_date = date.fromisoformat(payload.target_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid date format '{payload.target_date}'. Expected YYYY-MM-DD.",
            )

    try:
        analysis = await coordination_service.analyze_conflicts_and_strategies(
            user_id=user_id,
            target_date=target_date,
            explicit_tz=payload.timezone,
            window_hours=payload.window_hours,
            default_duration_minutes=payload.default_duration_minutes,
            work_start_hour=payload.work_start_hour,
            work_end_hour=payload.work_end_hour,
        )
        return ResolveConflictsResponse(
            analysis_id=analysis.analysis_id,
            target_date=analysis.target_date.isoformat(),
            timezone=analysis.timezone,
            conflicts_count=len(analysis.conflicts),
            strategy_options=analysis.strategy_options,
        )
    except InvalidTimezoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )


class ProposeDebriefRequest(BaseModel):
    raw_notes: str = Field("", description="Meeting notes or transcript")
    task_candidates: Optional[List[Dict[str, Any]]] = Field(None, description="Action items identified")
    reminder_candidates: Optional[List[Dict[str, Any]]] = Field(None, description="Follow-up reminders")
    memory_candidates: Optional[List[Dict[str, Any]]] = Field(None, description="Episodic takeaways")


@router.get(
    "/events/{event_id}/briefing",
    response_model=EventBriefing,
    summary="Get Event Briefing",
    description="Synthesizes read-only pre-event briefing for an upcoming calendar event.",
)
async def get_event_briefing_endpoint(
    event_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    briefing_service: EventBriefingService = Depends(get_briefing_service_dependency),
) -> EventBriefing:
    user_id = principal.user.user_id
    try:
        return await briefing_service.generate_briefing(event_id=event_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/events/{event_id}/debrief/propose",
    response_model=DebriefProposal,
    summary="Propose Event Debrief",
    description="Generates a server-held debrief proposal in DRAFT status. Does not execute mutations.",
)
async def propose_event_debrief_endpoint(
    event_id: str,
    payload: ProposeDebriefRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    briefing_service: EventBriefingService = Depends(get_briefing_service_dependency),
) -> DebriefProposal:
    user_id = principal.user.user_id
    try:
        return await briefing_service.propose_debrief(
            event_id=event_id,
            user_id=user_id,
            raw_notes=payload.raw_notes,
            task_candidates=payload.task_candidates,
            reminder_candidates=payload.reminder_candidates,
            memory_candidates=payload.memory_candidates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/debrief/proposals/{proposal_id}",
    response_model=DebriefProposal,
    summary="Get Debrief Proposal",
    description="Retrieves a server-held debrief proposal by ID.",
)
async def get_debrief_proposal_endpoint(
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    briefing_service: EventBriefingService = Depends(get_briefing_service_dependency),
) -> DebriefProposal:
    user_id = principal.user.user_id
    proposal = await briefing_service.get_proposal(proposal_id=proposal_id, user_id=user_id)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debrief proposal not found.")
    return proposal


@router.post(
    "/debrief/proposals/{proposal_id}/abort",
    response_model=DebriefProposal,
    summary="Abort Debrief Proposal",
    description="Aborts a pending debrief proposal (DRAFT -> ABORTED).",
)
async def abort_debrief_proposal_endpoint(
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    briefing_service: EventBriefingService = Depends(get_briefing_service_dependency),
) -> DebriefProposal:
    user_id = principal.user.user_id
    try:
        return await briefing_service.abort_proposal(proposal_id=proposal_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


class ApplyDebriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = Field(default=True)


@router.post(
    "/debrief/proposals/{proposal_id}/apply",
    response_model=DebriefProposal,
    summary="Apply Debrief Proposal",
    description="Executes or aborts a confirmed DebriefProposal under two-tier governed execution.",
)
async def apply_debrief_proposal_endpoint(
    request: Request,
    proposal_id: str,
    payload: ApplyDebriefRequest = Body(default_factory=ApplyDebriefRequest),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    briefing_service: EventBriefingService = Depends(get_briefing_service_dependency),
) -> DebriefProposal:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        return await briefing_service.execute_proposal(
            proposal_id=proposal_id,
            user_id=user_id,
            approved=payload.approved,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "INSUFFICIENT_PERMISSIONS", "message": str(exc)},
        )
    except DebriefStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ILLEGAL_STATE_TRANSITION", "message": str(exc)},
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower() or "cross-user" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "PROPOSAL_NOT_FOUND", "message": msg},
            )
        elif "expired" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PROPOSAL_EXPIRED", "message": msg},
            )
        elif "state has changed" in msg.lower() or "precondition" in msg.lower() or "no longer exists" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "STATE_CHANGED", "message": msg},
            )
        elif "cannot execute proposal in status" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "ILLEGAL_STATE_TRANSITION", "message": msg},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "UNPROCESSABLE_ENTITY", "message": msg},
            )
    except Exception as exc:
        logger.error("Unexpected error applying debrief proposal", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_SERVER_ERROR", "message": "Internal server error"},
        )


class ProposeDayPlanRequest(BaseModel):
    target_date: Optional[date] = None
    explicit_tz: Optional[str] = None
    focus_recommendations: Optional[list[dict[str, Any]]] = None
    milestone_reminders: Optional[list[dict[str, Any]]] = None


class ProposeEveningReviewRequest(BaseModel):
    target_date: Optional[date] = None
    explicit_tz: Optional[str] = None
    rollover_task_ids: Optional[list[str]] = None
    next_day_reminders: Optional[list[dict[str, Any]]] = None
    reflection_content: Optional[str] = None
    context_tags: Optional[list[str]] = None


# ── Phase 6G: Daily Horizon Orchestration Endpoints ──────────────────────────


class ApplyDayPlanRequest(BaseModel):
    proposal_id: str = Field(..., description="Authoritative server proposal ID")
    approved: bool = Field(..., description="Explicit confirmation: True to execute, False to abort")

    model_config = ConfigDict(extra="forbid")


class ApplyEveningReviewRequest(BaseModel):
    proposal_id: str = Field(..., description="Authoritative server proposal ID")
    approved: bool = Field(..., description="Explicit confirmation: True to execute, False to abort")

    model_config = ConfigDict(extra="forbid")


@router.get(
    "/daily/executive-plan",
    response_model=DailyExecutivePlan,
    summary="Get Daily Executive Plan",
    description="Synthesizes read-only executive morning day-plan.",
)
async def get_daily_executive_plan_endpoint(
    target_date: Optional[str] = Query(None, description="Target date YYYY-MM-DD"),
    explicit_tz: Optional[str] = Query(None, description="IANA timezone"),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
) -> DailyExecutivePlan:
    user_id = principal.user.user_id
    t_date = date.fromisoformat(target_date) if target_date else None
    try:
        return await horizon_service.get_daily_executive_plan(
            target_date=t_date,
            explicit_tz=explicit_tz,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/daily/plan/propose",
    response_model=DayPlanProposal,
    summary="Propose Daily Plan",
    description="Generates a server-held DayPlanProposal in DRAFT status.",
)
async def propose_daily_plan_endpoint(
    payload: ProposeDayPlanRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
) -> DayPlanProposal:
    user_id = principal.user.user_id
    try:
        return await horizon_service.propose_daily_plan(
            target_date=payload.target_date,
            explicit_tz=payload.explicit_tz,
            focus_recommendations=payload.focus_recommendations,
            milestone_reminders=payload.milestone_reminders,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/daily/plan/apply",
    response_model=DayPlanProposal,
    summary="Apply Daily Plan Proposal",
    description="Executes or aborts a confirmed DayPlanProposal under two-tier governed execution.",
)
async def apply_daily_plan_endpoint(
    request: Request,
    payload: ApplyDayPlanRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
) -> DayPlanProposal:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        proposal = await horizon_service.get_proposal(proposal_id=payload.proposal_id, user_id=user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Horizon proposal not found.")

    if not isinstance(proposal, DayPlanProposal):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Proposal '{payload.proposal_id}' is not a DayPlanProposal.",
        )

    try:
        result = await horizon_service.execute_proposal(
            proposal_id=payload.proposal_id,
            user_id=user_id,
            approved=payload.approved,
        )
        return result
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Horizon proposal not found.")
    except PreconditionFailedError as exc:
        transport_code = CANONICAL_TRANSPORT_CODE_MAP[exc.code]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": transport_code, "message": exc.message},
        )
    except HorizonStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ILLEGAL_STATE_TRANSITION", "message": str(exc)},
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception as exc:
        logger.error("Unexpected error applying daily plan proposal", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@router.get(
    "/daily/evening-review",
    response_model=EveningHorizonReview,
    summary="Get Evening Horizon Review",
    description="Synthesizes read-only evening review with completed/overdue items and rollover candidates.",
)
async def get_evening_horizon_review_endpoint(
    target_date: Optional[str] = Query(None, description="Target date YYYY-MM-DD"),
    explicit_tz: Optional[str] = Query(None, description="IANA timezone"),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
) -> EveningHorizonReview:
    user_id = principal.user.user_id
    t_date = date.fromisoformat(target_date) if target_date else None
    try:
        return await horizon_service.get_evening_horizon_review(
            target_date=t_date,
            explicit_tz=explicit_tz,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/daily/evening-review/propose",
    response_model=EveningReviewProposal,
    summary="Propose Evening Review",
    description="Generates a server-held EveningReviewProposal in DRAFT status.",
)
async def propose_evening_review_endpoint(
    payload: ProposeEveningReviewRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
) -> EveningReviewProposal:
    user_id = principal.user.user_id
    try:
        return await horizon_service.propose_evening_review(
            target_date=payload.target_date,
            explicit_tz=payload.explicit_tz,
            rollover_task_ids=payload.rollover_task_ids,
            next_day_reminders=payload.next_day_reminders,
            reflection_content=payload.reflection_content,
            context_tags=payload.context_tags,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/daily/evening-review/apply",
    response_model=EveningReviewProposal,
    summary="Apply Evening Review Proposal",
    description="Executes or aborts a confirmed EveningReviewProposal under two-tier governed execution.",
)
async def apply_evening_review_endpoint(
    request: Request,
    payload: ApplyEveningReviewRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
) -> EveningReviewProposal:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        proposal = await horizon_service.get_proposal(proposal_id=payload.proposal_id, user_id=user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Horizon proposal not found.")

    if not isinstance(proposal, EveningReviewProposal):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Proposal '{payload.proposal_id}' is not an EveningReviewProposal.",
        )

    try:
        result = await horizon_service.execute_proposal(
            proposal_id=payload.proposal_id,
            user_id=user_id,
            approved=payload.approved,
        )
        return result
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Horizon proposal not found.")
    except PreconditionFailedError as exc:
        transport_code = CANONICAL_TRANSPORT_CODE_MAP[exc.code]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": transport_code, "message": exc.message},
        )
    except HorizonStateTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ILLEGAL_STATE_TRANSITION", "message": str(exc)},
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception as exc:
        logger.error("Unexpected error applying evening review proposal", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")


@router.get(
    "/horizon/proposals/{proposal_id}",
    summary="Get Horizon Proposal",
    description="Retrieves a server-held DayPlanProposal or EveningReviewProposal by ID.",
)
async def get_horizon_proposal_endpoint(
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
):
    user_id = principal.user.user_id
    proposal = await horizon_service.get_proposal(proposal_id=proposal_id, user_id=user_id)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Horizon proposal not found.")
    return proposal


@router.post(
    "/horizon/proposals/{proposal_id}/abort",
    summary="Abort Horizon Proposal",
    description="Aborts a pending DayPlanProposal or EveningReviewProposal (DRAFT -> ABORTED).",
)
async def abort_horizon_proposal_endpoint(
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    horizon_service: ExecutiveHorizonOrchestrationService = Depends(get_horizon_service_dependency),
):
    user_id = principal.user.user_id
    try:
        return await horizon_service.abort_proposal(proposal_id=proposal_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


# ==============================================================================
# Phase 6H: Weekly Strategic Horizon & Multi-Day Planning Endpoints
# ==============================================================================

class ProposeWeeklyPlanRequest(BaseModel):
    task_allocations: Optional[List[Dict[str, Any]]] = None
    milestone_reminders: Optional[List[Dict[str, Any]]] = None
    strategic_intent_notes: Optional[str] = None
    target_start_date: Optional[str] = None
    explicit_tz: Optional[str] = None
    work_start_hour: Optional[int] = None
    work_end_hour: Optional[int] = None


class ApplyWeeklyPlanRequest(BaseModel):
    proposal_id: str
    approved: bool = True


class ProposeWeeklyRetrospectiveRequest(BaseModel):
    commitment_ids: Optional[List[str]] = None
    retrospective_notes: Optional[str] = None
    lookback_end_date: Optional[str] = None
    explicit_tz: Optional[str] = None


class ApplyWeeklyRetrospectiveRequest(BaseModel):
    proposal_id: str
    approved: bool = True


@router.get(
    "/weekly/horizon",
    response_model=WeeklyStrategicHorizon,
    summary="Get Weekly Strategic Horizon",
    description="Synthesizes a 7-day rolling horizon projection across Calendar, Tasks, and Commitments.",
)
async def get_weekly_horizon_endpoint(
    request: Request,
    target_start_date: Optional[str] = Query(None, alias="start_date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    explicit_tz: Optional[str] = Query(None, alias="timezone"),
    work_start_hour: Optional[int] = Query(None, ge=0, le=23),
    work_end_hour: Optional[int] = Query(None, ge=1, le=24),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
) -> WeeklyStrategicHorizon:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        return await weekly_service.get_weekly_strategic_horizon(
            target_start_date=target_start_date,
            explicit_tz=explicit_tz,
            work_start_hour=work_start_hour,
            work_end_hour=work_end_hour,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (ValueError, InvalidTimezoneError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/weekly/propose-plan",
    response_model=WeeklyPlanProposal,
    summary="Propose Weekly Plan",
    description="Synthesizes multi-day task allocations and milestone reminders into a server-held WeeklyPlanProposal in DRAFT status.",
)
async def propose_weekly_plan_endpoint(
    request: Request,
    payload: ProposeWeeklyPlanRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
) -> WeeklyPlanProposal:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        return await weekly_service.propose_weekly_plan(
            task_allocations=payload.task_allocations,
            milestone_reminders=payload.milestone_reminders,
            strategic_intent_notes=payload.strategic_intent_notes,
            target_start_date=payload.target_start_date,
            explicit_tz=payload.explicit_tz,
            work_start_hour=payload.work_start_hour,
            work_end_hour=payload.work_end_hour,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (ValueError, PreconditionFailedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/weekly/apply-plan",
    response_model=WeeklyPlanProposal,
    summary="Apply Weekly Plan",
    description="Executes a confirmed WeeklyPlanProposal under two-tier governance.",
)
async def apply_weekly_plan_endpoint(
    request: Request,
    payload: ApplyWeeklyPlanRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
) -> WeeklyPlanProposal:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        return await weekly_service.apply_weekly_plan(
            proposal_id=payload.proposal_id,
            approved=payload.approved,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (PreconditionFailedError, WeeklyStateTransitionError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/weekly/retrospective",
    response_model=WeeklyRetrospective,
    summary="Get Weekly Retrospective",
    description="Synthesizes a 7-day retrospective lookback over completed tasks, honored commitments, and evening reviews.",
)
async def get_weekly_retrospective_endpoint(
    request: Request,
    lookback_end_date: Optional[str] = Query(None, alias="end_date", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    explicit_tz: Optional[str] = Query(None, alias="timezone"),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
) -> WeeklyRetrospective:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        return await weekly_service.get_weekly_retrospective(
            lookback_end_date=lookback_end_date,
            explicit_tz=explicit_tz,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (ValueError, InvalidTimezoneError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/weekly/propose-retrospective",
    response_model=WeeklyRetrospectiveProposal,
    summary="Propose Weekly Retrospective",
    description="Proposes completion of verified honored commitments and retrospective memory commit.",
)
async def propose_weekly_retrospective_endpoint(
    request: Request,
    payload: ProposeWeeklyRetrospectiveRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
) -> WeeklyRetrospectiveProposal:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        return await weekly_service.propose_weekly_retrospective(
            commitment_ids=payload.commitment_ids,
            retrospective_notes=payload.retrospective_notes,
            lookback_end_date=payload.lookback_end_date,
            explicit_tz=payload.explicit_tz,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (ValueError, PreconditionFailedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/weekly/apply-retrospective",
    response_model=WeeklyRetrospectiveProposal,
    summary="Apply Weekly Retrospective",
    description="Executes a confirmed WeeklyRetrospectiveProposal: completes commitments and commits retrospective memory.",
)
async def apply_weekly_retrospective_endpoint(
    request: Request,
    payload: ApplyWeeklyRetrospectiveRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
) -> WeeklyRetrospectiveProposal:
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id

    try:
        return await weekly_service.apply_weekly_retrospective(
            proposal_id=payload.proposal_id,
            approved=payload.approved,
            user_id=user_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except (PreconditionFailedError, WeeklyStateTransitionError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get(
    "/weekly/proposals/{proposal_id}",
    summary="Get Weekly Proposal",
    description="Retrieves a server-held WeeklyPlanProposal or WeeklyRetrospectiveProposal by ID.",
)
async def get_weekly_proposal_endpoint(
    request: Request,
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
):
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id
    proposal = await weekly_service.get_proposal(proposal_id=proposal_id, user_id=user_id)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Weekly proposal not found.")
    return proposal


@router.post(
    "/weekly/proposals/{proposal_id}/abort",
    summary="Abort Weekly Proposal",
    description="Aborts a pending WeeklyPlanProposal or WeeklyRetrospectiveProposal.",
)
async def abort_weekly_proposal_endpoint(
    request: Request,
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    weekly_service: ExecutiveWeeklyHorizonService = Depends(get_weekly_horizon_service_dependency),
):
    _check_forbidden_identity_overrides(request)
    user_id = principal.user.user_id
    try:
        return await weekly_service.abort_proposal(proposal_id=proposal_id, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


