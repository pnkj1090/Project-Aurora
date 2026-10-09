from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import zoneinfo

from app.services.calendar.manager import CalendarManager, get_calendar_manager
from app.services.calendar.models import CalendarEvent
from app.services.commitments import CommitmentManager, get_commitment_manager
from app.services.commitments.models import Commitment, CommitmentStatus
from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.memory import MemoryManager, get_memory_manager
from app.services.memory.schemas import MemorySource, MemoryStatus, MemoryType
from app.services.permissions import PermissionManager, get_permission_manager
from app.services.permissions.models import PermissionType
from app.services.reminders import ReminderManager, get_reminder_manager
from app.services.reminders.models import Reminder
from app.services.tasks import TaskManager, get_task_manager
from app.services.tasks.models import Task, TaskPriority, TaskStatus

from app.services.agenda.service import AgendaService, get_agenda_service, InvalidTimezoneError
from app.services.agenda.coordination import resolve_task_duration
from app.services.agenda.horizon_models import (
    HORIZON_PROPOSAL_TTL_MINUTES,
    HORIZON_PROPOSAL_TTL_SECONDS,
    DailyExecutivePlan,
    DayPlanFocusRecommendation,
    DayPlanProposal,
    EveningHorizonReview,
    EveningReviewProposal,
    HorizonFailureReason,
    HorizonOperation,
    HorizonOperationPrecondition,
    HorizonOperationPreview,
    HorizonOperationStatus,
    HorizonOperationType,
    HorizonProposalPresentation,
    HorizonProposalStatus,
    HorizonProposalType,
    HorizonStateTransitionError,
    MemoryCreateExecutionPrecondition,
    PreconditionFailedError,
    ReminderCreateExecutionPrecondition,
    ReminderLinkType,
    TaskRolloverPrecondition,
    build_client_action,
    compute_horizon_state_hash,
    serialize_horizon_canonical_state,
    validate_horizon_state_transition,
)

logger = logging.getLogger("aurora.agenda.horizon")


def _enforce_authenticated_user_id(user_id: Optional[str]) -> str:
    """Enforces that authenticated user_id is explicitly provided and never defaults."""
    if not user_id or not user_id.strip() or user_id == DEFAULT_LOCAL_USER_ID:
        raise ValueError("Explicit authenticated user_id is required; DEFAULT_LOCAL_USER_ID is forbidden.")
    return user_id


class ExecutiveHorizonOrchestrationService:
    """
    Authoritative service governing Executive Day Planning and Daily Horizon Orchestration (Phase 6G).
    - Read-only morning executive day-plan synthesis (`get_daily_executive_plan`).
    - Server-held day-plan proposal creation (`propose_daily_plan`).
    - Read-only evening executive horizon review (`get_evening_horizon_review`).
    - Server-held evening review proposal creation (`propose_evening_review`).
    - Two-tier governed horizon proposal execution (`execute_proposal`).
    - Strict HorizonProposalStatus state machine.
    - Explicit authenticated user_id forwarding; DEFAULT_LOCAL_USER_ID fallback strictly prohibited.
    """

    def __init__(
        self,
        agenda_service: Optional[AgendaService] = None,
        calendar_manager: Optional[CalendarManager] = None,
        task_manager: Optional[TaskManager] = None,
        commitment_manager: Optional[CommitmentManager] = None,
        reminder_manager: Optional[ReminderManager] = None,
        memory_manager: Optional[MemoryManager] = None,
        permission_manager: Optional[PermissionManager] = None,
    ):
        self._agenda_service = agenda_service
        self._calendar_manager = calendar_manager
        self._task_manager = task_manager
        self._commitment_manager = commitment_manager
        self._reminder_manager = reminder_manager
        self._memory_manager = memory_manager
        self._permission_manager = permission_manager

        # In-memory proposal store keyed by proposal_id
        self._proposals: Dict[str, Union[DayPlanProposal, EveningReviewProposal]] = {}

    @property
    def agenda_service(self) -> AgendaService:
        return self._agenda_service or get_agenda_service()

    @property
    def calendar_manager(self) -> CalendarManager:
        return self._calendar_manager or get_calendar_manager()

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager or get_task_manager()

    @property
    def commitment_manager(self) -> CommitmentManager:
        return self._commitment_manager or get_commitment_manager()

    @property
    def reminder_manager(self) -> ReminderManager:
        return self._reminder_manager or get_reminder_manager()

    @property
    def memory_manager(self) -> MemoryManager:
        return self._memory_manager or get_memory_manager()

    @property
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager or get_permission_manager()

    def _resolve_working_hours(
        self,
        explicit_start: Optional[int] = None,
        explicit_end: Optional[int] = None,
    ) -> Tuple[int, int]:
        """
        Reuses Phase 6D working hours authority in AgendaService.
        Defaults to 9:00 and 18:00.
        """
        start = explicit_start if explicit_start is not None else 9
        end = explicit_end if explicit_end is not None else 18
        self.agenda_service._validate_working_hours(start, end)
        return start, end

    # ── 1. Morning Synthesis (Read-Only) ──────────────────────────────────────

    async def get_daily_executive_plan(
        self,
        target_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        explicit_timezone: Optional[str] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> DailyExecutivePlan:
        """
        Synthesizes today's schedule intervals, open tasks, active commitments,
        and scheduled reminders into an executive morning day-plan.
        Enforces read-permission matrix and Option B domain omission policy.
        """
        user_id = _enforce_authenticated_user_id(user_id)
        resolved_tz_str = explicit_timezone or explicit_tz
        effective_tz = await self.agenda_service.resolve_effective_timezone(resolved_tz_str, user_id=user_id)

        if target_date is None:
            eval_date = datetime.now(effective_tz).date()
        elif isinstance(target_date, str):
            eval_date = date.fromisoformat(target_date)
        else:
            eval_date = target_date

        # Check domain read permissions
        perms = {
            "calendar": await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id),
            "tasks": await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id),
            "commitments": await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id),
            "reminders": await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id),
        }

        if not any(perms.values()):
            raise PermissionError("No authorized domains for daily executive plan.")

        omitted_domains: List[Dict[str, str]] = []
        for domain, is_auth in perms.items():
            if not is_auth:
                omitted_domains.append({
                    "domain": domain,
                    "permission": domain,
                    "reason": "PERMISSION_DENIED"
                })

        # Window boundaries in UTC
        start_of_day_local = datetime(eval_date.year, eval_date.month, eval_date.day, 0, 0, 0, tzinfo=effective_tz)
        end_of_day_local = datetime(eval_date.year, eval_date.month, eval_date.day, 23, 59, 59, 999999, tzinfo=effective_tz)
        window_start_utc = start_of_day_local.astimezone(timezone.utc)
        window_end_utc = end_of_day_local.astimezone(timezone.utc)

        # 1. Calendar
        raw_events: List[CalendarEvent] = []
        schedule_intervals: List[Dict[str, Any]] = []
        free_slots_data: List[Dict[str, Any]] = []
        free_slots = []

        if perms["calendar"]:
            events_tuple = await self.calendar_manager.get_events(
                start_at=window_start_utc,
                end_at=window_end_utc,
                user_id=user_id
            )
            ev_list = events_tuple[0] or []
            raw_events = ev_list
            schedule_intervals = [
                {
                    "event_id": ev.event_id,
                    "title": ev.title,
                    "start_at": ev.start_at.isoformat(),
                    "end_at": ev.end_at.isoformat(),
                    "is_all_day": ev.all_day,
                }
                for ev in sorted(ev_list, key=lambda x: x.start_at)
            ]

            start_hour, end_hour = self._resolve_working_hours()
            free_slots = self.agenda_service._calculate_free_slots_for_day(
                target_date=eval_date,
                events=raw_events,
                effective_tz=effective_tz,
                work_start_hour=start_hour,
                work_end_hour=end_hour,
            )
            free_slots_data = [
                {
                    "start_at": slot.start_at.isoformat(),
                    "end_at": slot.end_at.isoformat(),
                    "duration_minutes": slot.duration_minutes,
                }
                for slot in free_slots
            ]

        # 2. Tasks
        open_tasks_data: List[Dict[str, Any]] = []
        if perms["tasks"]:
            all_tasks = await self.task_manager.list_tasks(user_id=user_id)
            open_tasks = [t for t in all_tasks if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]
            open_tasks_data = [
                {
                    "id": t.id,
                    "title": t.title,
                    "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                    "due_at": t.due_at.isoformat() if t.due_at else None,
                    "source_commitment_id": t.source_commitment_id,
                }
                for t in open_tasks
            ]

        # 3. Commitments
        active_commitments_data: List[Dict[str, Any]] = []
        if perms["commitments"]:
            all_cmts = await self.commitment_manager.list_commitments(user_id=user_id)
            active_cmts = [c for c in all_cmts if c.status == CommitmentStatus.ACTIVE]
            active_commitments_data = [
                {
                    "id": c.id,
                    "title": c.title,
                    "target_date": c.target_date.strftime("%Y-%m-%d") if c.target_date else None,
                    "priority": c.priority.value if hasattr(c.priority, "value") else str(c.priority),
                }
                for c in active_cmts
            ]

        # 4. Reminders
        scheduled_reminders_data: List[Dict[str, Any]] = []
        if perms["reminders"]:
            all_rems = await self.reminder_manager.list_reminders(user_id=user_id)
            rems = [
                r for r in all_rems
                if window_start_utc <= (r.scheduled_at if r.scheduled_at.tzinfo else r.scheduled_at.replace(tzinfo=timezone.utc)) <= window_end_utc
            ]
            scheduled_reminders_data = [
                {
                    "id": r.id,
                    "title": r.title,
                    "scheduled_at": r.scheduled_at.isoformat(),
                    "task_id": r.task_id,
                    "commitment_id": r.commitment_id,
                }
                for r in rems
            ]

        # Summary
        summary = (
            f"Executive day plan for {eval_date.isoformat()}: "
            f"{len(schedule_intervals)} events, {len(free_slots_data)} free slots, "
            f"{len(open_tasks_data)} open tasks, {len(active_commitments_data)} active commitments."
        )

        return DailyExecutivePlan(
            target_date=eval_date.isoformat(),
            user_id=user_id,
            schedule_intervals=schedule_intervals,
            open_tasks=open_tasks_data,
            active_commitments=active_commitments_data,
            scheduled_reminders=scheduled_reminders_data,
            free_slots=free_slots_data,
            focus_recommendations=[],
            omitted_domains=omitted_domains,
            summary=summary,
        )

    # ── 2. Day-Plan Proposal Creation ─────────────────────────────────────────

    async def propose_daily_plan(
        self,
        target_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        explicit_timezone: Optional[str] = None,
        focus_recommendations: Optional[List[Dict[str, Any]]] = None,
        milestone_reminders: Optional[List[Dict[str, Any]]] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> DayPlanProposal:
        """
        Creates an immutable, server-held DayPlanProposal in DRAFT status.
        Contains non-mutating focus recommendations and proposed milestone reminders.
        """
        user_id = _enforce_authenticated_user_id(user_id)
        resolved_tz_str = explicit_timezone or explicit_tz
        effective_tz = await self.agenda_service.resolve_effective_timezone(resolved_tz_str, user_id=user_id)

        if target_date is None:
            eval_date = datetime.now(effective_tz).date()
        elif isinstance(target_date, str):
            eval_date = date.fromisoformat(target_date)
        else:
            eval_date = target_date

        # Permission checks
        perms = {
            "calendar": await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id),
            "tasks": await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id),
            "commitments": await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id),
            "reminders": await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id),
        }

        if not any(perms.values()):
            raise PermissionError("No authorized domains for day plan proposal.")

        omitted_domains: List[Dict[str, str]] = []
        for domain, is_auth in perms.items():
            if not is_auth:
                omitted_domains.append({
                    "domain": domain,
                    "permission": domain,
                    "reason": "PERMISSION_DENIED"
                })

        # Process focus recommendations (presentation-only)
        recs: List[DayPlanFocusRecommendation] = []
        if focus_recommendations and perms["tasks"]:
            for r in focus_recommendations:
                slot_s = None
                slot_e = None
                s_raw = r.get("slot_start") or r.get("recommended_slot_start")
                e_raw = r.get("slot_end") or r.get("recommended_slot_end")
                if s_raw:
                    try:
                        slot_s = datetime.fromisoformat(s_raw) if "T" in s_raw else datetime.combine(eval_date, time.fromisoformat(s_raw), tzinfo=effective_tz)
                    except Exception:
                        slot_s = None
                if e_raw:
                    try:
                        slot_e = datetime.fromisoformat(e_raw) if "T" in e_raw else datetime.combine(eval_date, time.fromisoformat(e_raw), tzinfo=effective_tz)
                    except Exception:
                        slot_e = None

                dur = r.get("duration_minutes")
                if not dur and slot_s and slot_e:
                    dur = int((slot_e - slot_s).total_seconds() / 60)

                recs.append(DayPlanFocusRecommendation(
                    task_id=r["task_id"],
                    task_title=r.get("task_title", "Focus Task"),
                    slot_start=slot_s,
                    slot_end=slot_e,
                    duration_minutes=dur,
                    recommended_slot_start=r.get("recommended_slot_start"),
                    recommended_slot_end=r.get("recommended_slot_end"),
                    rationale=r.get("rationale"),
                    linked_commitment_id=r.get("linked_commitment_id"),
                ))

        # Process milestone reminders
        operations: List[HorizonOperation] = []
        referenced_tasks: List[Task] = []
        referenced_commitments: List[Commitment] = []

        if milestone_reminders and perms["reminders"]:
            for mr in milestone_reminders:
                sched_dt = mr["scheduled_at"] if isinstance(mr["scheduled_at"], datetime) else datetime.fromisoformat(mr["scheduled_at"])
                if sched_dt.tzinfo is None:
                    sched_dt = sched_dt.replace(tzinfo=timezone.utc)
                else:
                    sched_dt = sched_dt.astimezone(timezone.utc)

                # Early future-time validation
                if sched_dt <= datetime.now(timezone.utc):
                    raise ValueError("scheduled_at must be in the future.")

                l_type = ReminderLinkType(mr.get("linked_entity_type", "none"))
                l_id = mr.get("linked_entity_id")

                cal_s = mr.get("calendar_slot_start")
                cal_e = mr.get("calendar_slot_end")
                if cal_s and isinstance(cal_s, str):
                    cal_s = datetime.fromisoformat(cal_s)
                    if cal_s.tzinfo is None:
                        cal_s = cal_s.replace(tzinfo=timezone.utc)
                if cal_e and isinstance(cal_e, str):
                    cal_e = datetime.fromisoformat(cal_e)
                    if cal_e.tzinfo is None:
                        cal_e = cal_e.replace(tzinfo=timezone.utc)

                rem_pre = ReminderCreateExecutionPrecondition(
                    exact_title=mr["title"],
                    exact_scheduled_at=sched_dt,
                    linked_entity_type=l_type,
                    linked_entity_id=l_id,
                    calendar_slot_start=cal_s,
                    calendar_slot_end=cal_e,
                )

                op_pre = HorizonOperationPrecondition(
                    operation_type=HorizonOperationType.CREATE_REMINDER,
                    reminder_create=rem_pre,
                )

                op = HorizonOperation(
                    index=len(operations),
                    operation_type=HorizonOperationType.CREATE_REMINDER,
                    precondition=op_pre,
                    proposed_payload=dict(mr),
                )
                operations.append(op)

                if l_type == ReminderLinkType.TASK and l_id:
                    t = await self.task_manager.get_task(l_id, user_id=user_id)
                    if not t:
                        raise ValueError(f"Linked task '{l_id}' not found or access denied.")
                    if t not in referenced_tasks:
                        referenced_tasks.append(t)
                elif l_type == ReminderLinkType.COMMITMENT and l_id:
                    c = await self.commitment_manager.get_commitment(l_id, user_id=user_id)
                    if not c:
                        raise ValueError(f"Linked commitment '{l_id}' not found or access denied.")
                    if c not in referenced_commitments:
                        referenced_commitments.append(c)

        for rec in recs:
            t = await self.task_manager.get_task(rec.task_id, user_id=user_id)
            if t and t not in referenced_tasks:
                referenced_tasks.append(t)
            if rec.linked_commitment_id:
                c = await self.commitment_manager.get_commitment(rec.linked_commitment_id, user_id=user_id)
                if c and c not in referenced_commitments:
                    referenced_commitments.append(c)

        state_hash = compute_horizon_state_hash(referenced_tasks, referenced_commitments)
        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(minutes=HORIZON_PROPOSAL_TTL_MINUTES)

        proposal = DayPlanProposal(
            user_id=user_id,
            target_date=eval_date,
            expires_at=expires_at,
            status=HorizonProposalStatus.DRAFT,
            state_hash=state_hash,
            focus_recommendations=recs,
            ordered_operations=operations,
            omitted_domains=omitted_domains,
        )

        self._proposals[proposal.proposal_id] = proposal
        return proposal

    # ── 3. Evening Review (Read-Only) ─────────────────────────────────────────

    async def get_evening_horizon_review(
        self,
        target_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        explicit_timezone: Optional[str] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> EveningHorizonReview:
        """
        Synthesizes completed vs overdue tasks and commitments across today.
        Evaluates task rollover candidates and commitment overrun warnings.
        """
        user_id = _enforce_authenticated_user_id(user_id)
        resolved_tz_str = explicit_timezone or explicit_tz
        effective_tz = await self.agenda_service.resolve_effective_timezone(resolved_tz_str, user_id=user_id)

        if target_date is None:
            eval_date = datetime.now(effective_tz).date()
        elif isinstance(target_date, str):
            eval_date = date.fromisoformat(target_date)
        else:
            eval_date = target_date

        perms = {
            "tasks": await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id),
            "commitments": await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id),
            "reminders": await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id),
        }

        if not any(perms.values()):
            raise PermissionError("No authorized domains for evening horizon review.")

        omitted_domains: List[Dict[str, str]] = []
        for domain, is_auth in perms.items():
            if not is_auth:
                omitted_domains.append({
                    "domain": domain,
                    "permission": domain,
                    "reason": "PERMISSION_DENIED"
                })

        start_of_day_local = datetime(eval_date.year, eval_date.month, eval_date.day, 0, 0, 0, tzinfo=effective_tz)
        end_of_day_local = datetime(eval_date.year, eval_date.month, eval_date.day, 23, 59, 59, 999999, tzinfo=effective_tz)
        window_start_utc = start_of_day_local.astimezone(timezone.utc)
        window_end_utc = end_of_day_local.astimezone(timezone.utc)
        now_utc = datetime.now(timezone.utc)

        completed_tasks_data: List[Dict[str, Any]] = []
        overdue_tasks_data: List[Dict[str, Any]] = []
        rollover_candidates_data: List[Dict[str, Any]] = []

        if perms["tasks"]:
            all_tasks = await self.task_manager.list_tasks(user_id=user_id)
            for t in all_tasks:
                if t.status == TaskStatus.COMPLETED:
                    completed_tasks_data.append({
                        "id": t.id,
                        "title": t.title,
                        "updated_at": t.updated_at.isoformat(),
                    })
                elif t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                    if t.due_at:
                        t_due_utc = t.due_at if t.due_at.tzinfo else t.due_at.replace(tzinfo=timezone.utc)
                        if t_due_utc < now_utc:
                            overdue_tasks_data.append({
                                "id": t.id,
                                "title": t.title,
                                "due_at": t.due_at.isoformat(),
                                "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                                "source_commitment_id": t.source_commitment_id,
                            })
                            rollover_candidates_data.append({
                                "task_id": t.id,
                                "title": t.title,
                                "priority": t.priority.value if hasattr(t.priority, "value") else str(t.priority),
                            })

        completed_commitments_data: List[Dict[str, Any]] = []
        active_commitments_data: List[Dict[str, Any]] = []
        if perms["commitments"]:
            all_cmts = await self.commitment_manager.list_commitments(user_id=user_id)
            for c in all_cmts:
                if c.status == CommitmentStatus.COMPLETED:
                    completed_commitments_data.append({
                        "id": c.id,
                        "title": c.title,
                    })
                elif c.status == CommitmentStatus.ACTIVE:
                    active_commitments_data.append({
                        "id": c.id,
                        "title": c.title,
                        "target_date": c.target_date.strftime("%Y-%m-%d") if c.target_date else None,
                    })

        scheduled_reminders_data: List[Dict[str, Any]] = []
        if perms["reminders"]:
            all_rems = await self.reminder_manager.list_reminders(user_id=user_id)
            rems = [
                r for r in all_rems
                if window_start_utc <= (r.scheduled_at if r.scheduled_at.tzinfo else r.scheduled_at.replace(tzinfo=timezone.utc)) <= window_end_utc
            ]
            scheduled_reminders_data = [
                {
                    "id": r.id,
                    "title": r.title,
                    "scheduled_at": r.scheduled_at.isoformat(),
                }
                for r in rems
            ]

        total_tracked = len(completed_tasks_data) + len(overdue_tasks_data)
        rate = (len(completed_tasks_data) / total_tracked * 100.0) if total_tracked > 0 else 0.0
        stats = {
            "completed_count": len(completed_tasks_data),
            "overdue_count": len(overdue_tasks_data),
            "completion_rate": round(rate, 1),
        }

        summary = (
            f"Evening horizon review for {eval_date.isoformat()}: "
            f"{len(completed_tasks_data)} tasks completed, {len(overdue_tasks_data)} overdue."
        )

        return EveningHorizonReview(
            target_date=eval_date.isoformat(),
            user_id=user_id,
            completed_tasks=completed_tasks_data,
            overdue_tasks=overdue_tasks_data,
            completed_commitments=completed_commitments_data,
            active_commitments=active_commitments_data,
            scheduled_reminders=scheduled_reminders_data,
            completion_stats=stats,
            rollover_candidates=rollover_candidates_data,
            omitted_domains=omitted_domains,
            summary=summary,
        )

    # ── 4. Evening Review Proposal Creation ───────────────────────────────────

    async def propose_evening_review(
        self,
        target_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        explicit_timezone: Optional[str] = None,
        rollover_task_ids: Optional[List[str]] = None,
        rollover_tasks: Optional[List[Union[str, Dict[str, Any]]]] = None,
        next_day_reminders: Optional[List[Dict[str, Any]]] = None,
        reflection_content: Optional[str] = None,
        reflection_notes: Optional[str] = None,
        context_tags: Optional[List[str]] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> EveningReviewProposal:
        """
        Creates an immutable, server-held EveningReviewProposal in DRAFT status.
        Contains task rollovers, next-day reminders, and executive reflection memories.
        """
        user_id = _enforce_authenticated_user_id(user_id)
        resolved_tz_str = explicit_timezone or explicit_tz
        effective_tz = await self.agenda_service.resolve_effective_timezone(resolved_tz_str, user_id=user_id)

        if target_date is None:
            eval_date = datetime.now(effective_tz).date()
        elif isinstance(target_date, str):
            eval_date = date.fromisoformat(target_date)
        else:
            eval_date = target_date

        tomorrow_date = eval_date + timedelta(days=1)

        # Harmonize rollover task inputs
        if rollover_tasks:
            extracted_ids = []
            for item in rollover_tasks:
                if isinstance(item, str):
                    extracted_ids.append(item)
                elif isinstance(item, dict):
                    t_id = item.get("task_id") or item.get("id")
                    if t_id:
                        extracted_ids.append(t_id)
            rollover_task_ids = (rollover_task_ids or []) + extracted_ids

        # Harmonize reflection notes input
        if reflection_notes and not reflection_content:
            reflection_content = reflection_notes

        perms = {
            "tasks": await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id),
            "commitments": await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id),
            "reminders": await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id),
        }

        omitted_domains: List[Dict[str, str]] = []
        for domain, is_auth in perms.items():
            if not is_auth:
                omitted_domains.append({
                    "domain": domain,
                    "permission": domain,
                    "reason": "PERMISSION_DENIED"
                })

        operations: List[HorizonOperation] = []
        referenced_tasks: List[Task] = []
        referenced_commitments: List[Commitment] = []

        # 1. Rollover Tasks
        if rollover_task_ids and perms["tasks"]:
            # Retrieve working hours authority
            wh_start = 9
            wh_end = 18
            try:
                res_wh = self.agenda_service.resolve_working_hours(user_id)
                if res_wh:
                    wh_start = res_wh[0].hour if hasattr(res_wh[0], "hour") else int(res_wh[0])
                    wh_end = res_wh[1].hour if hasattr(res_wh[1], "hour") else int(res_wh[1])
            except Exception:
                wh_start, wh_end = self._resolve_working_hours()

            for t_id in rollover_task_ids:
                task = await self.task_manager.get_task(t_id, user_id=user_id)
                if not task:
                    raise ValueError(f"Task '{t_id}' not found or access denied.")
                referenced_tasks.append(task)

                if task.due_at:
                    t_due_local = task.due_at.astimezone(effective_tz) if task.due_at.tzinfo else task.due_at.replace(tzinfo=timezone.utc).astimezone(effective_tz)
                    due_time = t_due_local.time()
                    if time(wh_start, 0, 0) <= due_time <= time(wh_end, 0, 0):
                        selected_time = due_time
                    else:
                        selected_time = time(wh_end, 0, 0)
                else:
                    selected_time = time(wh_end, 0, 0)

                new_due_local = datetime.combine(tomorrow_date, selected_time, tzinfo=effective_tz)
                exact_new_due_at = new_due_local.astimezone(timezone.utc)

                # Check commitment consistency
                overrun_warning = None
                if task.source_commitment_id and perms["commitments"]:
                    cmt = await self.commitment_manager.get_commitment(task.source_commitment_id, user_id=user_id)
                    if cmt:
                        referenced_commitments.append(cmt)
                        cmt_target_date = cmt.target_date.date() if isinstance(cmt.target_date, datetime) else cmt.target_date
                        if cmt_target_date and tomorrow_date > cmt_target_date:
                            overrun_warning = (
                                f"Rollover date ({tomorrow_date.isoformat()}) exceeds linked commitment "
                                f"'{cmt.title}' target date ({cmt_target_date.isoformat()})."
                            )

                rollover_pre = TaskRolloverPrecondition(
                    task_id=task.id,
                    exact_new_due_at=exact_new_due_at,
                    exact_new_priority=None,
                )
                op_pre = HorizonOperationPrecondition(
                    operation_type=HorizonOperationType.ROLLOVER_TASK,
                    task_rollover=rollover_pre,
                )
                payload = {
                    "task_id": task.id,
                    "title": task.title,
                    "exact_new_due_at": exact_new_due_at.isoformat(),
                }
                if overrun_warning:
                    payload["warning"] = overrun_warning
                    payload["warnings"] = [overrun_warning]
                    payload["source_commitment_id"] = task.source_commitment_id
                    payload["commitment_id"] = task.source_commitment_id

                operations.append(HorizonOperation(
                    index=len(operations),
                    operation_type=HorizonOperationType.ROLLOVER_TASK,
                    precondition=op_pre,
                    proposed_payload=payload,
                ))

        # 2. Next-Day Reminders
        if next_day_reminders and perms["reminders"]:
            for rem in next_day_reminders:
                sched_dt = rem["scheduled_at"] if isinstance(rem["scheduled_at"], datetime) else datetime.fromisoformat(rem["scheduled_at"])
                if sched_dt.tzinfo is None:
                    sched_dt = sched_dt.replace(tzinfo=timezone.utc)
                else:
                    sched_dt = sched_dt.astimezone(timezone.utc)

                if sched_dt <= datetime.now(timezone.utc):
                    raise ValueError("scheduled_at must be in the future.")

                l_type = ReminderLinkType(rem.get("linked_entity_type", "none"))
                l_id = rem.get("linked_entity_id")

                cal_s = rem.get("calendar_slot_start")
                cal_e = rem.get("calendar_slot_end")
                if cal_s and isinstance(cal_s, str):
                    cal_s = datetime.fromisoformat(cal_s)
                    if cal_s.tzinfo is None:
                        cal_s = cal_s.replace(tzinfo=timezone.utc)
                if cal_e and isinstance(cal_e, str):
                    cal_e = datetime.fromisoformat(cal_e)
                    if cal_e.tzinfo is None:
                        cal_e = cal_e.replace(tzinfo=timezone.utc)

                rem_pre = ReminderCreateExecutionPrecondition(
                    exact_title=rem["title"],
                    exact_scheduled_at=sched_dt,
                    linked_entity_type=l_type,
                    linked_entity_id=l_id,
                    calendar_slot_start=cal_s,
                    calendar_slot_end=cal_e,
                )

                op_pre = HorizonOperationPrecondition(
                    operation_type=HorizonOperationType.CREATE_REMINDER,
                    reminder_create=rem_pre,
                )

                operations.append(HorizonOperation(
                    index=len(operations),
                    operation_type=HorizonOperationType.CREATE_REMINDER,
                    precondition=op_pre,
                    proposed_payload=dict(rem),
                ))

                if l_type == ReminderLinkType.TASK and l_id:
                    t = await self.task_manager.get_task(l_id, user_id=user_id)
                    if not t:
                        raise ValueError(f"Linked task '{l_id}' not found or access denied.")
                    if t not in referenced_tasks:
                        referenced_tasks.append(t)
                elif l_type == ReminderLinkType.COMMITMENT and l_id:
                    c = await self.commitment_manager.get_commitment(l_id, user_id=user_id)
                    if not c:
                        raise ValueError(f"Linked commitment '{l_id}' not found or access denied.")
                    if c not in referenced_commitments:
                        referenced_commitments.append(c)

        # 3. Reflection Memories
        if reflection_content and reflection_content.strip():
            tags = list(context_tags or [])
            if "horizon_review" not in tags:
                tags.append("horizon_review")

            mem_pre = MemoryCreateExecutionPrecondition(
                exact_content=reflection_content.strip(),
                context_tags=tags,
                source_horizon_type="evening_review",
            )
            op_pre = HorizonOperationPrecondition(
                operation_type=HorizonOperationType.CREATE_MEMORY,
                memory_create=mem_pre,
            )
            operations.append(HorizonOperation(
                index=len(operations),
                operation_type=HorizonOperationType.CREATE_MEMORY,
                precondition=op_pre,
                proposed_payload={
                    "content": reflection_content.strip(),
                    "tags": tags,
                },
            ))

        state_hash = compute_horizon_state_hash(referenced_tasks, referenced_commitments)
        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(minutes=HORIZON_PROPOSAL_TTL_MINUTES)

        proposal = EveningReviewProposal(
            user_id=user_id,
            target_date=eval_date,
            expires_at=expires_at,
            status=HorizonProposalStatus.DRAFT,
            state_hash=state_hash,
            ordered_operations=operations,
            omitted_domains=omitted_domains,
        )

        self._proposals[proposal.proposal_id] = proposal
        return proposal

    # ── 5. Proposal Retrieval, Approval & Abort ───────────────────────────────

    async def get_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Union[DayPlanProposal, EveningReviewProposal]:
        """Retrieves a proposal by ID scoped strictly to the authenticated user."""
        user_id = _enforce_authenticated_user_id(user_id)
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.user_id != user_id:
            raise ValueError(f"Proposal '{proposal_id}' not found.")
        return proposal

    async def approve_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Union[DayPlanProposal, EveningReviewProposal]:
        """Approves a pending proposal (DRAFT -> APPROVED)."""
        user_id = _enforce_authenticated_user_id(user_id)
        proposal = await self.get_proposal(proposal_id, user_id=user_id)
        validate_horizon_state_transition(proposal.status, HorizonProposalStatus.APPROVED)
        proposal.status = HorizonProposalStatus.APPROVED
        return proposal

    async def abort_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Union[DayPlanProposal, EveningReviewProposal]:
        """Aborts a pending proposal (DRAFT -> ABORTED)."""
        user_id = _enforce_authenticated_user_id(user_id)
        proposal = await self.get_proposal(proposal_id, user_id=user_id)
        validate_horizon_state_transition(proposal.status, HorizonProposalStatus.ABORTED)
        proposal.status = HorizonProposalStatus.ABORTED
        return proposal

    # ── 6. Two-Tier Governed Execution ────────────────────────────────────────

    async def execute_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        approved: bool = True,
    ) -> Union[DayPlanProposal, EveningReviewProposal]:
        """
        Executes a server-held proposal following explicit confirmation.
        Tier 1: Preflight validation (TTL, permissions, state hash, calendar TOCTOU, early future-time).
        Tier 2: Sequential dispatch with FINAL mutation-boundary future-time validation on reminders.
        """
        user_id = _enforce_authenticated_user_id(user_id)

        proposal = await self.get_proposal(proposal_id, user_id=user_id)

        if not approved:
            return await self.abort_proposal(proposal_id, user_id=user_id)

        # Transition DRAFT -> APPROVED if still draft
        if proposal.status == HorizonProposalStatus.DRAFT:
            validate_horizon_state_transition(proposal.status, HorizonProposalStatus.APPROVED)
            proposal.status = HorizonProposalStatus.APPROVED

        # ── TIER 1: PREFLIGHT VALIDATION ──────────────────────────────────────
        now_utc = datetime.now(timezone.utc)

        # 1. TTL Check
        if now_utc > proposal.expires_at:
            validate_horizon_state_transition(proposal.status, HorizonProposalStatus.EXPIRED)
            proposal.status = HorizonProposalStatus.EXPIRED
            proposal.failure_reason = HorizonFailureReason.EXPIRED
            raise PreconditionFailedError(
                code=HorizonFailureReason.EXPIRED,
                message="Proposal has expired.",
            )

        # 2. Permissions Check
        has_task_ops = any(op.operation_type == HorizonOperationType.ROLLOVER_TASK for op in proposal.ordered_operations)
        has_rem_ops = any(op.operation_type == HorizonOperationType.CREATE_REMINDER for op in proposal.ordered_operations)

        if has_task_ops:
            is_tasks_auth = await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
            if not is_tasks_auth:
                validate_horizon_state_transition(proposal.status, HorizonProposalStatus.FAILED)
                proposal.status = HorizonProposalStatus.FAILED
                proposal.failure_reason = HorizonFailureReason.INSUFFICIENT_PERMISSIONS
                raise PermissionError("TASKS permission is required for task rollover.")

        if has_rem_ops:
            is_rem_auth = await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)
            if not is_rem_auth:
                validate_horizon_state_transition(proposal.status, HorizonProposalStatus.FAILED)
                proposal.status = HorizonProposalStatus.FAILED
                proposal.failure_reason = HorizonFailureReason.INSUFFICIENT_PERMISSIONS
                raise PermissionError("REMINDERS permission is required for reminder creation.")

        # 3. Early Future-Time Check
        for op in proposal.ordered_operations:
            if op.operation_type == HorizonOperationType.CREATE_REMINDER:
                r_pre = op.precondition.reminder_create
                if r_pre.exact_scheduled_at <= now_utc:
                    validate_horizon_state_transition(proposal.status, HorizonProposalStatus.FAILED)
                    proposal.status = HorizonProposalStatus.FAILED
                    proposal.failure_reason = HorizonFailureReason.SCHEDULED_TIME_IN_PAST
                    raise PreconditionFailedError(
                        code=HorizonFailureReason.SCHEDULED_TIME_IN_PAST,
                        message="SCHEDULED_TIME_IN_PAST",
                    )

        # 4. State Hash Verification
        referenced_tasks: List[Task] = []
        referenced_cmts: List[Commitment] = []

        for op in proposal.ordered_operations:
            if op.operation_type == HorizonOperationType.ROLLOVER_TASK:
                t = await self.task_manager.get_task(op.precondition.task_rollover.task_id, user_id=user_id)
                if t:
                    referenced_tasks.append(t)
                    if t.source_commitment_id:
                        c = await self.commitment_manager.get_commitment(t.source_commitment_id, user_id=user_id)
                        if c and c not in referenced_cmts:
                            referenced_cmts.append(c)
            elif op.operation_type == HorizonOperationType.CREATE_REMINDER:
                rem_pre = op.precondition.reminder_create
                if rem_pre.linked_entity_type == ReminderLinkType.TASK and rem_pre.linked_entity_id:
                    t = await self.task_manager.get_task(rem_pre.linked_entity_id, user_id=user_id)
                    if t and t not in referenced_tasks:
                        referenced_tasks.append(t)
                elif rem_pre.linked_entity_type == ReminderLinkType.COMMITMENT and rem_pre.linked_entity_id:
                    c = await self.commitment_manager.get_commitment(rem_pre.linked_entity_id, user_id=user_id)
                    if c and c not in referenced_cmts:
                        referenced_cmts.append(c)

        current_hash = compute_horizon_state_hash(referenced_tasks, referenced_cmts)
        if current_hash != proposal.state_hash:
            validate_horizon_state_transition(proposal.status, HorizonProposalStatus.FAILED)
            proposal.status = HorizonProposalStatus.FAILED
            proposal.failure_reason = HorizonFailureReason.STATE_CHANGED
            raise PreconditionFailedError(
                code=HorizonFailureReason.STATE_CHANGED,
                message="STALE_STATE_HASH",
            )

        # 5. Calendar TOCTOU Check
        is_cal_auth = await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id)
        if is_cal_auth:
            for op in proposal.ordered_operations:
                if op.operation_type == HorizonOperationType.CREATE_REMINDER:
                    r_pre = op.precondition.reminder_create
                    if r_pre.calendar_slot_start and r_pre.calendar_slot_end:
                        evs, _ = await self.calendar_manager.get_events(
                            start_at=r_pre.calendar_slot_start,
                            end_at=r_pre.calendar_slot_end,
                            user_id=user_id,
                        )
                        ev_list = evs or []
                        slot_s = r_pre.calendar_slot_start.astimezone(timezone.utc)
                        slot_e = r_pre.calendar_slot_end.astimezone(timezone.utc)
                        overlap = any(
                            (e.end_at.astimezone(timezone.utc) > slot_s and e.start_at.astimezone(timezone.utc) < slot_e)
                            for e in ev_list
                        )
                        if overlap:
                            validate_horizon_state_transition(proposal.status, HorizonProposalStatus.FAILED)
                            proposal.status = HorizonProposalStatus.FAILED
                            proposal.failure_reason = HorizonFailureReason.STALE_CALENDAR_STATE
                            raise PreconditionFailedError(
                                code=HorizonFailureReason.STALE_CALENDAR_STATE,
                                message="STALE_CALENDAR_STATE",
                            )

        # ── TIER 2: SEQUENTIAL DISPATCH ───────────────────────────────────────
        validate_horizon_state_transition(proposal.status, HorizonProposalStatus.EXECUTING)
        proposal.status = HorizonProposalStatus.EXECUTING

        for op_idx, op in enumerate(proposal.ordered_operations):
            try:
                if op.operation_type == HorizonOperationType.ROLLOVER_TASK:
                    t_pre = op.precondition.task_rollover
                    updated_task = await self.task_manager.update_task(
                        task_id=t_pre.task_id,
                        user_id=user_id,
                        due_at=t_pre.exact_new_due_at,
                        priority=t_pre.exact_new_priority,
                    )
                    op.status = HorizonOperationStatus.COMPLETED
                    op.created_entity_id = updated_task.id if updated_task else t_pre.task_id

                elif op.operation_type == HorizonOperationType.CREATE_REMINDER:
                    r_pre = op.precondition.reminder_create

                    resolved_task_id = r_pre.linked_entity_id if r_pre.linked_entity_type == ReminderLinkType.TASK else None
                    resolved_cmt_id = r_pre.linked_entity_id if r_pre.linked_entity_type == ReminderLinkType.COMMITMENT else None

                    # ── STEP 8: FINAL MUTATION-BOUNDARY FUTURE-TIME VALIDATION ──
                    # Authoritative temporal guard directly preceding mutation.
                    # Zero await, database query, network operation, or async boundary.
                    now_utc = datetime.now(timezone.utc)
                    if r_pre.exact_scheduled_at <= now_utc:
                        raise PreconditionFailedError(
                            code=HorizonFailureReason.SCHEDULED_TIME_IN_PAST,
                            message="SCHEDULED_TIME_IN_PAST",
                        )

                    # ── STEP 9: AUTHORITATIVE MUTATION DISPATCH ────────────────
                    reminder = await self.reminder_manager.create_reminder(
                        title=r_pre.exact_title,
                        scheduled_at=r_pre.exact_scheduled_at,
                        user_id=user_id,
                        task_id=resolved_task_id,
                        commitment_id=resolved_cmt_id,
                    )
                    op.status = HorizonOperationStatus.COMPLETED
                    op.created_entity_id = reminder.id
                    op.proposed_payload["client_action"] = build_client_action("schedule_local_notification", reminder)

                elif op.operation_type == HorizonOperationType.CREATE_MEMORY:
                    m_pre = op.precondition.memory_create
                    mem_item, _ = await self.memory_manager.create_memory(
                        content=m_pre.exact_content,
                        type=MemoryType.EPISODIC,
                        importance=3,
                        confidence=1.0,
                        source=MemorySource.USER_EXPLICIT,
                        status=MemoryStatus.ACTIVE,
                        tags=m_pre.context_tags,
                        user_id=user_id,
                    )
                    op.status = HorizonOperationStatus.COMPLETED
                    op.created_entity_id = mem_item.id

            except Exception as exc:
                op.status = HorizonOperationStatus.FAILED
                op.failure_error = str(exc)

                if op_idx == 0:
                    validate_horizon_state_transition(proposal.status, HorizonProposalStatus.FAILED)
                    proposal.status = HorizonProposalStatus.FAILED
                    if "SCHEDULED_TIME_IN_PAST" in str(exc):
                        proposal.failure_reason = HorizonFailureReason.SCHEDULED_TIME_IN_PAST
                    else:
                        proposal.failure_reason = HorizonFailureReason.OPERATION_FAILED

                    for remaining in proposal.ordered_operations[op_idx + 1:]:
                        remaining.status = HorizonOperationStatus.SKIPPED
                else:
                    validate_horizon_state_transition(proposal.status, HorizonProposalStatus.PARTIALLY_EXECUTED)
                    proposal.status = HorizonProposalStatus.PARTIALLY_EXECUTED
                    proposal.failure_reason = HorizonFailureReason.OPERATION_FAILED

                    for remaining in proposal.ordered_operations[op_idx + 1:]:
                        remaining.status = HorizonOperationStatus.SKIPPED

                return proposal

        # All operations completed
        validate_horizon_state_transition(proposal.status, HorizonProposalStatus.COMPLETED)
        proposal.status = HorizonProposalStatus.COMPLETED
        proposal.applied_at = datetime.now(timezone.utc)
        return proposal


# ── Dependency Provider ───────────────────────────────────────────────────────

_default_horizon_service: Optional[ExecutiveHorizonOrchestrationService] = None


def get_horizon_service() -> ExecutiveHorizonOrchestrationService:
    global _default_horizon_service
    if _default_horizon_service is None:
        _default_horizon_service = ExecutiveHorizonOrchestrationService()
    return _default_horizon_service


def get_horizon_service_dependency() -> ExecutiveHorizonOrchestrationService:
    return get_horizon_service()
