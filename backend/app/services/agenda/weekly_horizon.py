from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union
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
from app.services.agenda.weekly_horizon_models import (
    WEEKLY_PROPOSAL_TTL_SECONDS,
    CommitmentHealthItem,
    DayCapacitySummary,
    IntraProposalDependencyPostState,
    PreconditionFailedError,
    ReminderLinkType,
    TaskAllocatePrecondition,
    WeeklyCommitmentCompletePrecondition,
    WeeklyFailureReason,
    WeeklyHorizonCongestionLevel,
    WeeklyMemoryCreatePrecondition,
    WeeklyOperation,
    WeeklyOperationStatus,
    WeeklyOperationType,
    WeeklyPlanProposal,
    WeeklyProposalStatus,
    WeeklyProposalType,
    WeeklyReminderCreatePrecondition,
    WeeklyRetrospective,
    WeeklyRetrospectiveProposal,
    WeeklyStateTransitionError,
    WeeklyStrategicHorizon,
    compute_intra_proposal_fingerprint,
    extract_canonical_task_payload,
    validate_weekly_state_transition,
)

logger = logging.getLogger("aurora.agenda.weekly_horizon")


def _enforce_authenticated_user_id(user_id: Optional[str]) -> str:
    """Enforces that authenticated user_id is explicitly provided and never defaults."""
    if not user_id or not user_id.strip() or user_id == DEFAULT_LOCAL_USER_ID:
        raise ValueError("Explicit authenticated user_id is required; DEFAULT_LOCAL_USER_ID is forbidden.")
    return user_id.strip()


def build_client_action(action_type: str, reminder: Reminder) -> Dict[str, Any]:
    """Constructs Phase 6B schedule_local_notification client action."""
    return {
        "action_type": action_type,
        "payload": {
            "notification_id": reminder.notification_id or reminder.id,
            "title": reminder.title,
            "body": reminder.body or "",
            "scheduled_at": reminder.scheduled_at.astimezone(timezone.utc).isoformat(),
            "reminder_id": reminder.id,
            "task_id": reminder.task_id,
            "commitment_id": reminder.commitment_id,
        }
    }


class ExecutiveWeeklyHorizonService:
    """
    Authoritative service governing Weekly Strategic Horizon & Multi-Day Planning Orchestration (Phase 6H).
    - 7-Day Forward Horizon Synthesis (`get_weekly_strategic_horizon`).
    - Multi-Day Task Allocation & Milestone Reminder Proposals (`propose_weekly_plan`).
    - Governed Two-Tier Plan Execution (`apply_weekly_plan`).
    - 7-Day Retrospective Lookback Synthesis (`get_weekly_retrospective`).
    - Retrospective Proposal & Commitment Completion (`propose_weekly_retrospective`, `apply_weekly_retrospective`).
    - Strict IntraProposalDependencyPostState verification for intra-proposal dependencies.
    - Strict operation-level permission dynamic union and execution revalidation.
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
        self._proposals: Dict[str, Union[WeeklyPlanProposal, WeeklyRetrospectiveProposal]] = {}
        # Concurrency locks keyed by proposal_id
        self._locks: Dict[str, asyncio.Lock] = {}

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
        Reuses Phase 6D working hours validation in AgendaService.
        Defaults to 9:00 and 18:00.
        """
        start = explicit_start if explicit_start is not None else 9
        end = explicit_end if explicit_end is not None else 18
        self.agenda_service._validate_working_hours(start, end)
        return start, end

    def _get_lock(self, proposal_id: str) -> asyncio.Lock:
        if proposal_id not in self._locks:
            self._locks[proposal_id] = asyncio.Lock()
        return self._locks[proposal_id]

    # ── State Hash Computation ───────────────────────────────────────────────

    def _compute_operation_dependency_hash(
        self,
        op_type: WeeklyOperationType,
        precondition: Any,
        dependency_state: Dict[str, Any],
    ) -> str:
        """
        Targeted entity state hash computation.
        Decoupled from calendar; hashes only exact domain dependencies.
        """
        payload = {
            "op_type": op_type.value,
            "dependency_state": dependency_state,
        }
        if isinstance(precondition, TaskAllocatePrecondition):
            payload["effective_work_start_hour"] = precondition.effective_work_start_hour
            payload["effective_work_end_hour"] = precondition.effective_work_end_hour
            payload["target_date"] = precondition.target_date.isoformat()
            payload["allocated_slot_start"] = precondition.allocated_slot_start.isoformat()
            payload["allocated_slot_end"] = precondition.allocated_slot_end.isoformat()
        elif isinstance(precondition, WeeklyReminderCreatePrecondition):
            payload["exact_title"] = precondition.exact_title
            payload["exact_scheduled_at"] = precondition.exact_scheduled_at.isoformat()
        elif isinstance(precondition, WeeklyMemoryCreatePrecondition):
            payload["content"] = precondition.content.strip().lower()
            payload["tags"] = sorted(precondition.tags)
        elif isinstance(precondition, WeeklyCommitmentCompletePrecondition):
            payload["commitment_id"] = precondition.commitment_id
            payload["expected_status"] = precondition.expected_status.value

        return compute_intra_proposal_fingerprint(payload)

    def _compute_proposal_state_hash(self, operations: List[WeeklyOperation]) -> str:
        hashes = [
            f"{op.operation_id}:{self._compute_operation_dependency_hash(op.operation_type, op.precondition, op.dependency_state_snapshot)}"
            for op in sorted(operations, key=lambda o: o.execution_order)
        ]
        return compute_intra_proposal_fingerprint({"op_hashes": hashes})

    # ── 1. Weekly Strategic Horizon Read ─────────────────────────────────────

    async def get_weekly_strategic_horizon(
        self,
        target_start_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        work_start_hour: Optional[int] = None,
        work_end_hour: Optional[int] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> WeeklyStrategicHorizon:
        user_id = _enforce_authenticated_user_id(user_id)
        tz = await self.agenda_service.resolve_effective_timezone(explicit_tz, user_id=user_id)

        # Check permissions
        has_calendar = await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id)
        has_tasks = await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
        has_commitments = await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id)

        omitted_domains: List[Dict[str, str]] = []
        if not has_calendar and not has_tasks:
            raise PermissionError("Access denied: neither CALENDAR nor TASKS permission is authorized.")

        if not has_calendar:
            omitted_domains.append({"domain": "calendar", "permission": "calendar", "reason": "CALENDAR_UNAUTHORIZED"})
        if not has_tasks:
            omitted_domains.append({"domain": "tasks", "permission": "tasks", "reason": "TASKS_UNAUTHORIZED"})
        if not has_commitments:
            omitted_domains.append({"domain": "commitments", "permission": "commitments", "reason": "COMMITMENTS_UNAUTHORIZED"})

        # Resolve 7-day start date
        if target_start_date is None:
            now_local = datetime.now(tz)
            start_d = now_local.date()
        elif isinstance(target_start_date, str):
            try:
                start_d = date.fromisoformat(target_start_date)
            except ValueError:
                raise ValueError(f"Invalid target_start_date format: {target_start_date}. Expected YYYY-MM-DD.")
        else:
            start_d = target_start_date

        end_d = start_d + timedelta(days=6)
        wh_start, wh_end = self._resolve_working_hours(work_start_hour, work_end_hour)
        daily_work_minutes = (wh_end - wh_start) * 60

        # Query tasks across rolling 7 days if authorized
        all_tasks: List[Task] = []
        if has_tasks:
            all_tasks = await self.task_manager.list_tasks(user_id=user_id, limit=200)

        # Query commitments if authorized
        all_commitments: List[Commitment] = []
        if has_commitments:
            all_commitments = await self.commitment_manager.list_commitments(user_id=user_id, limit=100)

        day_summaries: List[DayCapacitySummary] = []
        total_free_minutes = 0
        overcommitted_count = 0

        for day_offset in range(7):
            cur_date = start_d + timedelta(days=day_offset)
            date_str = cur_date.isoformat()
            day_name = cur_date.strftime("%A")

            # Calculate calendar busy minutes within working hours
            events_count = 0
            busy_minutes = 0
            if has_calendar:
                day_start_dt = datetime.combine(cur_date, time(wh_start, 0), tzinfo=tz)
                day_end_dt = datetime.combine(cur_date, time(wh_end, 0), tzinfo=tz)
                events, _ = await self.calendar_manager.get_events(start_at=day_start_dt, end_at=day_end_dt, user_id=user_id)
                events = events or []
                events_count = len(events)
                for ev in events:
                    # Bound to working hours
                    ev_s = max(ev.start_at.astimezone(tz), day_start_dt)
                    ev_e = min(ev.end_at.astimezone(tz), day_end_dt)
                    if ev_s < ev_e:
                        busy_minutes += int((ev_e - ev_s).total_seconds() / 60)

            # Calculate task duration allocated on this day
            cur_tasks: List[Task] = []
            task_minutes = 0
            if has_tasks:
                for t in all_tasks:
                    if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS) and t.due_at:
                        t_local_date = t.due_at.astimezone(tz).date()
                        if t_local_date == cur_date:
                            cur_tasks.append(t)
                            task_minutes += resolve_task_duration(t, 30)

            allocated_focus = busy_minutes + task_minutes
            free_focus = max(0, daily_work_minutes - allocated_focus)
            total_free_minutes += free_focus
            is_overcommitted = allocated_focus > daily_work_minutes
            if is_overcommitted:
                overcommitted_count += 1

            if free_focus < 120 or allocated_focus > 420:
                congestion = WeeklyHorizonCongestionLevel.CONGESTED
            elif free_focus > 360:
                congestion = WeeklyHorizonCongestionLevel.LIGHT
            else:
                congestion = WeeklyHorizonCongestionLevel.BALANCED

            day_summaries.append(
                DayCapacitySummary(
                    date_str=date_str,
                    day_of_week=day_name,
                    total_focus_minutes=daily_work_minutes,
                    allocated_focus_minutes=allocated_focus,
                    free_focus_minutes=free_focus,
                    events_count=events_count,
                    tasks_count=len(cur_tasks),
                    congestion_level=congestion,
                    is_overcommitted=is_overcommitted,
                )
            )

        # Audit commitment health
        commitment_health: List[CommitmentHealthItem] = []
        orphan_count = 0
        active_commitments = [c for c in all_commitments if c.status == CommitmentStatus.ACTIVE]

        for cmt in active_commitments:
            linked = [t for t in all_tasks if t.source_commitment_id == cmt.id]
            completed_linked = [t for t in linked if t.status == TaskStatus.COMPLETED]
            open_linked = [t for t in linked if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]

            # Orphan check: target date in rolling 7-day window and 0 linked tasks
            is_orphan = False
            cmt_d = cmt.target_date.date() if (cmt.target_date and isinstance(cmt.target_date, datetime)) else cmt.target_date
            if cmt_d and start_d <= cmt_d <= end_d and not linked:
                is_orphan = True
                orphan_count += 1

            # Overrun risk: any task scheduled past commitment target_date
            has_overrun_risk = False
            overrun_warning: Optional[str] = None
            if cmt_d:
                for t in open_linked:
                    if t.due_at:
                        t_date = t.due_at.astimezone(tz).date()
                        if t_date > cmt_d:
                            has_overrun_risk = True
                            overrun_warning = f"Task '{t.title}' due on {t_date.isoformat()} exceeds commitment deadline {cmt_d.isoformat()}."
                            break

            commitment_health.append(
                CommitmentHealthItem(
                    commitment_id=cmt.id,
                    title=cmt.title,
                    target_date=cmt_d,
                    linked_tasks_count=len(linked),
                    completed_tasks_count=len(completed_linked),
                    open_tasks_count=len(open_linked),
                    is_orphan=is_orphan,
                    has_overrun_risk=has_overrun_risk,
                    overrun_warning=overrun_warning,
                )
            )

        # Synthesize strategic recommendations
        recommendations: List[str] = []
        if orphan_count > 0:
            recommendations.append(f"Create actionable tasks for {orphan_count} orphan commitment(s) due this week.")
        if overcommitted_count > 0:
            recommendations.append(f"Reallocate task due dates on {overcommitted_count} overcommitted day(s) to preserve focus reserves.")
        overrun_commitments = [c for c in commitment_health if c.has_overrun_risk]
        if overrun_commitments:
            recommendations.append(f"Resolve deadline overruns for {len(overrun_commitments)} commitment(s) with tasks scheduled past target dates.")
        if not recommendations:
            recommendations.append("Horizon is balanced; maintain planned focus allocations.")

        return WeeklyStrategicHorizon(
            user_id=user_id,
            timezone=str(tz),
            horizon_start_date=start_d.isoformat(),
            horizon_end_date=end_d.isoformat(),
            day_summaries=day_summaries,
            commitment_health=commitment_health,
            orphan_commitments_count=orphan_count,
            overcommitted_days_count=overcommitted_count,
            total_free_focus_minutes=total_free_minutes,
            strategic_recommendations=recommendations[:3],
            omitted_domains=omitted_domains,
        )

    # ── 2. Propose Weekly Plan ───────────────────────────────────────────────

    async def propose_weekly_plan(
        self,
        task_allocations: Optional[List[Dict[str, Any]]] = None,
        milestone_reminders: Optional[List[Dict[str, Any]]] = None,
        strategic_intent_notes: Optional[str] = None,
        target_start_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        work_start_hour: Optional[int] = None,
        work_end_hour: Optional[int] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> WeeklyPlanProposal:
        user_id = _enforce_authenticated_user_id(user_id)
        tz = await self.agenda_service.resolve_effective_timezone(explicit_tz, user_id=user_id)
        wh_start, wh_end = self._resolve_working_hours(work_start_hour, work_end_hour)

        has_tasks = await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
        has_calendar = await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id)
        has_reminders = await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)

        omitted_domains: List[Dict[str, str]] = []
        operations: List[WeeklyOperation] = []
        order = 0

        # Task Allocation Operations
        task_allocations = task_allocations or []
        for alloc in task_allocations:
            task_id = alloc.get("task_id")
            target_date_raw = alloc.get("target_date")
            if not task_id or not target_date_raw:
                continue

            target_d = date.fromisoformat(str(target_date_raw))

            # Permission gating: Task allocation requires TASKS + CALENDAR
            if not has_calendar:
                if not any(d["reason"] == "TASK_ALLOCATION_OMITTED_CALENDAR_UNAUTHORIZED" for d in omitted_domains):
                    omitted_domains.append({"domain": "calendar", "permission": "calendar", "reason": "TASK_ALLOCATION_OMITTED_CALENDAR_UNAUTHORIZED"})
                continue
            if not has_tasks:
                if not any(d["reason"] == "TASKS_UNAUTHORIZED" for d in omitted_domains):
                    omitted_domains.append({"domain": "tasks", "permission": "tasks", "reason": "TASKS_UNAUTHORIZED"})
                continue

            # Query task
            task = await self.task_manager.get_task(task_id=task_id, user_id=user_id)
            if not task:
                raise ValueError(f"Task '{task_id}' not found or access denied.")

            duration = resolve_task_duration(task, 30)

            # Query free slot
            slot_res = await self.agenda_service.propose_schedule_slot(
                user_id=user_id,
                task_id=task.id,
                duration_minutes=duration,
                date_str=target_d.isoformat(),
                explicit_tz=str(tz),
                work_start_hour=wh_start,
                work_end_hour=wh_end,
            )

            if not slot_res.feasible or not slot_res.proposed_slots:
                reason = slot_res.reason or "Slot infeasible"
                raise PreconditionFailedError(f"No available schedule slot for task '{task.id}' on {target_d.isoformat()}: {reason}.")

            slot_start = slot_res.proposed_slots[0].start_at
            slot_end = slot_res.proposed_slots[0].end_at

            # Read parent commitment if linked
            parent_target_date: Optional[date] = None
            parent_cmt_state: Dict[str, Any] = {}
            if task.source_commitment_id:
                cmt = await self.commitment_manager.get_commitment(commitment_id=task.source_commitment_id, user_id=user_id)
                if cmt:
                    parent_target_date = cmt.target_date.date() if (cmt.target_date and isinstance(cmt.target_date, datetime)) else cmt.target_date
                    parent_cmt_state = {
                        "id": cmt.id,
                        "status": cmt.status.value,
                        "target_date": parent_target_date.isoformat() if parent_target_date else None,
                        "updated_at": cmt.updated_at.isoformat() if cmt.updated_at else None,
                    }

            precond = TaskAllocatePrecondition(
                task_id=task.id,
                expected_status=task.status,
                expected_due_at=task.due_at,
                expected_source_commitment_id=task.source_commitment_id,
                target_date=target_d,
                allocated_slot_start=slot_start,
                allocated_slot_end=slot_end,
                effective_duration_minutes=duration,
                parent_commitment_target_date=parent_target_date,
                effective_work_start_hour=wh_start,
                effective_work_end_hour=wh_end,
            )

            dep_state = {
                "task": extract_canonical_task_payload(task),
                "parent_commitment": parent_cmt_state,
            }

            operations.append(
                WeeklyOperation(
                    operation_id=f"op_{order:02d}_alloc_{task.id}",
                    operation_type=WeeklyOperationType.ALLOCATE_TASK_DUE_DATE,
                    execution_order=order,
                    required_permissions=[PermissionType.TASKS, PermissionType.CALENDAR],
                    precondition=precond,
                    dependency_entities=[{"type": "task", "id": task.id}],
                    dependency_state_snapshot=dep_state,
                )
            )
            order += 1

        # Milestone Reminder Operations
        milestone_reminders = milestone_reminders or []
        for r_spec in milestone_reminders:
            title = r_spec.get("title", "").strip()
            sched_raw = r_spec.get("scheduled_at")
            if not title or not sched_raw:
                continue

            if isinstance(sched_raw, str):
                sched_dt = datetime.fromisoformat(sched_raw)
            else:
                sched_dt = sched_raw

            if sched_dt.tzinfo is None:
                sched_dt = sched_dt.replace(tzinfo=timezone.utc)
            else:
                sched_dt = sched_dt.astimezone(timezone.utc)

            # Tier 1 Future-Time Check
            now_utc = datetime.now(timezone.utc)
            if sched_dt <= now_utc:
                raise PreconditionFailedError("Tier-1 check failed: scheduled_at must be strictly in the future.")

            link_type_raw = r_spec.get("linked_entity_type", "none").lower()
            link_type = ReminderLinkType(link_type_raw)
            link_id = r_spec.get("linked_entity_id")

            # Verify linked entity ownership and capture state
            dep_entities: List[Dict[str, str]] = []
            dep_state: Dict[str, Any] = {}
            if link_type == ReminderLinkType.TASK and link_id:
                linked_task = await self.task_manager.get_task(task_id=link_id, user_id=user_id)
                if not linked_task:
                    raise ValueError(f"Linked task '{link_id}' not found or access denied.")
                dep_entities.append({"type": "task", "id": link_id})
                dep_state["linked_task"] = extract_canonical_task_payload(linked_task)
            elif link_type == ReminderLinkType.COMMITMENT and link_id:
                linked_cmt = await self.commitment_manager.get_commitment(commitment_id=link_id, user_id=user_id)
                if not linked_cmt:
                    raise ValueError(f"Linked commitment '{link_id}' not found or access denied.")
                dep_entities.append({"type": "commitment", "id": link_id})
                dep_state["linked_commitment"] = {
                    "id": linked_cmt.id,
                    "status": linked_cmt.status.value,
                    "updated_at": linked_cmt.updated_at.isoformat() if linked_cmt.updated_at else None,
                }

            cal_slot_start = r_spec.get("calendar_slot_start")
            cal_slot_end = r_spec.get("calendar_slot_end")
            req_perms = [PermissionType.REMINDERS]
            if cal_slot_start:
                req_perms.append(PermissionType.CALENDAR)

            r_precond = WeeklyReminderCreatePrecondition(
                exact_title=title,
                exact_scheduled_at=sched_dt,
                linked_entity_type=link_type,
                linked_entity_id=link_id,
                calendar_slot_start=cal_slot_start,
                calendar_slot_end=cal_slot_end,
            )

            operations.append(
                WeeklyOperation(
                    operation_id=f"op_{order:02d}_rem_{uuid.uuid4().hex[:6]}",
                    operation_type=WeeklyOperationType.CREATE_MILESTONE_REMINDER,
                    execution_order=order,
                    required_permissions=req_perms,
                    precondition=r_precond,
                    dependency_entities=dep_entities,
                    dependency_state_snapshot=dep_state,
                )
            )
            order += 1

        # Strategic Memory Operation (Option A: only when explicit notes provided)
        if strategic_intent_notes and strategic_intent_notes.strip():
            mem_precond = WeeklyMemoryCreatePrecondition(
                content=strategic_intent_notes.strip(),
                importance=3,
                confidence=1.0,
                source=MemorySource.USER_EXPLICIT,
                status=MemoryStatus.ACTIVE,
                tags=["weekly_horizon", "strategic_plan"],
            )
            operations.append(
                WeeklyOperation(
                    operation_id=f"op_{order:02d}_mem_{uuid.uuid4().hex[:6]}",
                    operation_type=WeeklyOperationType.CREATE_STRATEGIC_MEMORY,
                    execution_order=order,
                    required_permissions=[],  # Gated by authenticated identity + confirmation
                    precondition=mem_precond,
                    dependency_entities=[],
                    dependency_state_snapshot={},
                )
            )
            order += 1

        # Dynamic proposal permission union
        union_permissions: Set[PermissionType] = set()
        for op in operations:
            union_permissions.update(op.required_permissions)

        # Compute proposal state hash
        state_hash = self._compute_proposal_state_hash(operations)

        now = datetime.now(timezone.utc)
        proposal_id = f"wpr_{uuid.uuid4().hex[:12]}"
        proposal = WeeklyPlanProposal(
            proposal_id=proposal_id,
            user_id=user_id,
            proposal_type=WeeklyProposalType.WEEKLY_PLAN,
            status=WeeklyProposalStatus.DRAFT,
            created_at=now,
            expires_at=now + timedelta(seconds=WEEKLY_PROPOSAL_TTL_SECONDS),
            state_hash=state_hash,
            required_permissions=sorted(list(union_permissions), key=lambda p: p.value),
            operations=operations,
            omitted_domains=omitted_domains,
            metadata={
                "target_start_date": str(target_start_date) if target_start_date else None,
                "timezone": str(tz),
                "work_start_hour": wh_start,
                "work_end_hour": wh_end,
            },
        )

        self._proposals[proposal_id] = proposal
        return proposal

    # ── 3. Governed Two-Tier Plan Execution ──────────────────────────────────

    async def apply_weekly_plan(
        self,
        proposal_id: str,
        approved: bool = True,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> WeeklyPlanProposal:
        user_id = _enforce_authenticated_user_id(user_id)
        proposal = self._proposals.get(proposal_id)
        if not proposal or proposal.user_id != user_id:
            raise ValueError("Weekly proposal not found or access denied.")

        if not isinstance(proposal, WeeklyPlanProposal):
            raise ValueError(f"Proposal '{proposal_id}' is not a WeeklyPlanProposal.")

        async with self._get_lock(proposal_id):
            now_utc = datetime.now(timezone.utc)
            if now_utc >= proposal.expires_at:
                proposal.status = WeeklyProposalStatus.EXPIRED
                raise PreconditionFailedError(f"Weekly proposal '{proposal_id}' has expired.")

            if not approved:
                validate_weekly_state_transition(proposal.status, WeeklyProposalStatus.ABORTED)
                proposal.status = WeeklyProposalStatus.ABORTED
                return proposal

            # Proposal transition DRAFT -> APPROVED -> EXECUTING
            if proposal.status == WeeklyProposalStatus.DRAFT:
                validate_weekly_state_transition(proposal.status, WeeklyProposalStatus.APPROVED)
                proposal.status = WeeklyProposalStatus.APPROVED

            validate_weekly_state_transition(proposal.status, WeeklyProposalStatus.EXECUTING)
            proposal.status = WeeklyProposalStatus.EXECUTING

            # ── GLOBAL PREFLIGHT ──────────────────────────────────────────────
            # 1. Revalidate proposal-level union permissions
            for perm in proposal.required_permissions:
                if not await self.permission_manager.is_authorized(perm, user_id=user_id):
                    proposal.status = WeeklyProposalStatus.FAILED
                    raise PreconditionFailedError(f"PERMISSION_REVOKED: Permission '{perm.value}' is no longer authorized.")

            # Identify entities intentionally mutated by earlier operations in this proposal
            producer_mutated_task_ids: Set[str] = set()
            for op in proposal.operations:
                if op.operation_type == WeeklyOperationType.ALLOCATE_TASK_DUE_DATE:
                    assert isinstance(op.precondition, TaskAllocatePrecondition)
                    producer_mutated_task_ids.add(op.precondition.task_id)

            # 2. Validate EXTERNAL_STATE_DEPENDENCY items across operations
            for op in proposal.operations:
                for dep in op.dependency_entities:
                    dep_type = dep.get("type")
                    dep_id = dep.get("id")
                    if not dep_type or not dep_id:
                        continue

                    # Defer INTRA_PROPOSAL_DEPENDENCY to consumer operation boundary
                    if dep_type == "task" and dep_id in producer_mutated_task_ids and op.execution_order > 0:
                        continue

                    if dep_type == "task":
                        curr_t = await self.task_manager.get_task(task_id=dep_id, user_id=user_id)
                        if not curr_t:
                            proposal.status = WeeklyProposalStatus.FAILED
                            raise PreconditionFailedError(f"STATE_HASH_MISMATCH: Task '{dep_id}' was deleted.")
                        curr_payload = extract_canonical_task_payload(curr_t)
                        expected_payload = op.dependency_state_snapshot.get("task") or op.dependency_state_snapshot.get("linked_task")
                        if expected_payload and compute_intra_proposal_fingerprint(curr_payload) != compute_intra_proposal_fingerprint(expected_payload):
                            proposal.status = WeeklyProposalStatus.FAILED
                            raise PreconditionFailedError(f"STATE_HASH_MISMATCH: Task '{dep_id}' was externally modified before execution.")

                    elif dep_type == "commitment":
                        curr_c = await self.commitment_manager.get_commitment(commitment_id=dep_id, user_id=user_id)
                        if not curr_c:
                            proposal.status = WeeklyProposalStatus.FAILED
                            raise PreconditionFailedError(f"STATE_HASH_MISMATCH: Commitment '{dep_id}' was deleted.")

            # ── SEQUENTIAL OPERATION EXECUTION ────────────────────────────────
            executed_ops_count = 0
            for op in sorted(proposal.operations, key=lambda o: o.execution_order):
                try:
                    # 1. Per-operation permission revalidation
                    for p in op.required_permissions:
                        if not await self.permission_manager.is_authorized(p, user_id=user_id):
                            raise PreconditionFailedError(f"PERMISSION_REVOKED: Permission '{p.value}' revoked before operation execution.")

                    # 2. Validate dependencies
                    for dep in op.dependency_entities:
                        dep_type = dep.get("type")
                        dep_id = dep.get("id")
                        if dep_type == "task" and dep_id in proposal.post_states:
                            # INTRA_PROPOSAL_DEPENDENCY: must match producer-generated post-state fingerprint!
                            curr_t = await self.task_manager.get_task(task_id=dep_id, user_id=user_id)
                            if not curr_t:
                                raise PreconditionFailedError(f"STATE_HASH_MISMATCH: Task '{dep_id}' not found.")
                            curr_fingerprint = compute_intra_proposal_fingerprint(extract_canonical_task_payload(curr_t))
                            expected_fingerprint = proposal.post_states[dep_id].post_state_fingerprint
                            if curr_fingerprint != expected_fingerprint:
                                raise PreconditionFailedError(f"STATE_HASH_MISMATCH: Task '{dep_id}' post-state fingerprint mismatch.")

                    # 3. Authoritative mutation dispatch
                    if op.operation_type == WeeklyOperationType.ALLOCATE_TASK_DUE_DATE:
                        assert isinstance(op.precondition, TaskAllocatePrecondition)
                        p_alloc = op.precondition

                        # Re-verify working hours configuration
                        curr_wh_s, curr_wh_e = self._resolve_working_hours(
                            proposal.metadata.get("work_start_hour"),
                            proposal.metadata.get("work_end_hour"),
                        )
                        if curr_wh_s != p_alloc.effective_work_start_hour or curr_wh_e != p_alloc.effective_work_end_hour:
                            raise PreconditionFailedError("WORKING_HOURS_MISMATCH: Effective working hours configuration changed.")

                        # Calendar TOCTOU revalidation
                        cal_events, _ = await self.calendar_manager.get_events(
                            start_at=p_alloc.allocated_slot_start,
                            end_at=p_alloc.allocated_slot_end,
                            user_id=user_id,
                        )
                        cal_events = cal_events or []
                        for ev in cal_events:
                            if ev.start_at < p_alloc.allocated_slot_end and ev.end_at > p_alloc.allocated_slot_start:
                                raise PreconditionFailedError("CALENDAR_SLOT_COLLISION: Intervening calendar event overlaps allocated slot.")

                        # Authoritative mutation: due_at = allocated_slot_end
                        updated_task = await self.task_manager.update_task(
                            task_id=p_alloc.task_id,
                            user_id=user_id,
                            due_at=p_alloc.allocated_slot_end,
                        )
                        if not updated_task:
                            raise RuntimeError(f"Failed to update task '{p_alloc.task_id}'.")

                        # Capture IntraProposalDependencyPostState immediately after mutation
                        fresh_task = await self.task_manager.get_task(task_id=p_alloc.task_id, user_id=user_id)
                        assert fresh_task is not None
                        canonical_payload = extract_canonical_task_payload(fresh_task)
                        post_fingerprint = compute_intra_proposal_fingerprint(canonical_payload)

                        post_state = IntraProposalDependencyPostState(
                            proposal_id=proposal.proposal_id,
                            producer_operation_id=op.operation_id,
                            entity_type="TASK",
                            entity_id=fresh_task.id,
                            captured_at=datetime.now(timezone.utc),
                            canonical_payload=canonical_payload,
                            post_state_fingerprint=post_fingerprint,
                        )
                        proposal.post_states[fresh_task.id] = post_state
                        op.created_entity_id = fresh_task.id

                    elif op.operation_type == WeeklyOperationType.CREATE_MILESTONE_REMINDER:
                        assert isinstance(op.precondition, WeeklyReminderCreatePrecondition)
                        r_pre = op.precondition

                        # Optional slot-tied calendar TOCTOU
                        if r_pre.calendar_slot_start and r_pre.calendar_slot_end:
                            cal_events, _ = await self.calendar_manager.get_events(
                                start_at=r_pre.calendar_slot_start,
                                end_at=r_pre.calendar_slot_end,
                                user_id=user_id,
                            )
                            cal_events = cal_events or []
                            for ev in cal_events:
                                if ev.start_at < r_pre.calendar_slot_end and ev.end_at > r_pre.calendar_slot_start:
                                    raise PreconditionFailedError("CALENDAR_SLOT_COLLISION: Event overlaps reminder slot.")

                        # ── STEP 8: FINAL MUTATION-BOUNDARY FUTURE-TIME VALIDATION ──
                        now_utc = datetime.now(timezone.utc)
                        if r_pre.exact_scheduled_at <= now_utc:
                            raise PreconditionFailedError("SCHEDULED_TIME_IN_PAST")

                        # ── STEP 9: AUTHORITATIVE MUTATION DISPATCH ────────────────
                        res_task_id = r_pre.linked_entity_id if r_pre.linked_entity_type == ReminderLinkType.TASK else None
                        res_cmt_id = r_pre.linked_entity_id if r_pre.linked_entity_type == ReminderLinkType.COMMITMENT else None
                        reminder = await self.reminder_manager.create_reminder(
                            title=r_pre.exact_title,
                            scheduled_at=r_pre.exact_scheduled_at,
                            user_id=user_id,
                            task_id=res_task_id,
                            commitment_id=res_cmt_id,
                        )
                        op.created_entity_id = reminder.id

                    elif op.operation_type == WeeklyOperationType.CREATE_STRATEGIC_MEMORY:
                        assert isinstance(op.precondition, WeeklyMemoryCreatePrecondition)
                        m_pre = op.precondition
                        mem_item, _ = await self.memory_manager.create_memory(
                            content=m_pre.content,
                            type=MemoryType.EPISODIC,
                            importance=m_pre.importance,
                            confidence=m_pre.confidence,
                            source=MemorySource.USER_EXPLICIT,
                            status=MemoryStatus.ACTIVE,
                            tags=m_pre.tags,
                            user_id=user_id,
                        )
                        op.created_entity_id = mem_item.id

                    op.status = WeeklyOperationStatus.COMPLETED
                    executed_ops_count += 1

                except Exception as exc:
                    logger.error(f"Operation {op.operation_id} failed: {exc}", exc_info=True)
                    op.status = WeeklyOperationStatus.FAILED
                    op.error_message = str(exc)

                    # Mark subsequent operations as SKIPPED
                    for subsequent in proposal.operations:
                        if subsequent.execution_order > op.execution_order:
                            subsequent.status = WeeklyOperationStatus.SKIPPED

                    if executed_ops_count == 0:
                        proposal.status = WeeklyProposalStatus.FAILED
                    else:
                        proposal.status = WeeklyProposalStatus.PARTIALLY_EXECUTED

                    if isinstance(exc, (PreconditionFailedError, WeeklyStateTransitionError)):
                        raise exc
                    raise PreconditionFailedError(f"Operation '{op.operation_id}' failed: {exc}") from exc

            proposal.status = WeeklyProposalStatus.EXECUTED
            return proposal

    # ── 4. Weekly Retrospective Read ─────────────────────────────────────────

    async def get_weekly_retrospective(
        self,
        lookback_end_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> WeeklyRetrospective:
        user_id = _enforce_authenticated_user_id(user_id)
        tz = await self.agenda_service.resolve_effective_timezone(explicit_tz, user_id=user_id)

        has_tasks = await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
        has_commitments = await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id)

        omitted_domains: List[Dict[str, str]] = []
        if not has_tasks:
            omitted_domains.append({"domain": "tasks", "permission": "tasks", "reason": "TASKS_UNAUTHORIZED"})
        if not has_commitments:
            omitted_domains.append({"domain": "commitments", "permission": "commitments", "reason": "COMMITMENTS_UNAUTHORIZED"})

        if lookback_end_date is None:
            now_local = datetime.now(tz)
            end_d = now_local.date()
        elif isinstance(lookback_end_date, str):
            try:
                end_d = date.fromisoformat(lookback_end_date)
            except ValueError:
                raise ValueError(f"Invalid lookback_end_date format: {lookback_end_date}. Expected YYYY-MM-DD.")
        else:
            end_d = lookback_end_date

        start_d = end_d - timedelta(days=6)

        # Query tasks
        completed_tasks: List[Dict[str, Any]] = []
        recurring_rollover: List[Dict[str, Any]] = []
        all_tasks: List[Task] = []
        if has_tasks:
            all_tasks = await self.task_manager.list_tasks(user_id=user_id, limit=200)
            for t in all_tasks:
                if t.status == TaskStatus.COMPLETED and t.updated_at:
                    t_d = t.updated_at.astimezone(tz).date()
                    if start_d <= t_d <= end_d:
                        completed_tasks.append({
                            "id": t.id,
                            "title": t.title,
                            "completed_at": t.updated_at.isoformat(),
                            "source_commitment_id": t.source_commitment_id,
                        })
                # Rollover check
                rollover_cnt = (t.metadata or {}).get("rollover_count", 0) if t.metadata else 0
                if rollover_cnt >= 2:
                    recurring_rollover.append({
                        "id": t.id,
                        "title": t.title,
                        "rollover_count": rollover_cnt,
                    })

        # Query commitments
        honored_commitments: List[Dict[str, Any]] = []
        unfulfilled_commitments: List[Dict[str, Any]] = []
        if has_commitments:
            all_commitments = await self.commitment_manager.list_commitments(user_id=user_id, limit=100)
            for cmt in all_commitments:
                linked = [t for t in all_tasks if t.source_commitment_id == cmt.id]
                open_linked = [t for t in linked if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]

                if cmt.status == CommitmentStatus.COMPLETED and cmt.updated_at:
                    c_d = cmt.updated_at.astimezone(tz).date()
                    if start_d <= c_d <= end_d:
                        honored_commitments.append({
                            "id": cmt.id,
                            "title": cmt.title,
                            "target_date": cmt.target_date.isoformat() if cmt.target_date else None,
                            "completed_at": cmt.updated_at.isoformat(),
                        })
                elif cmt.status == CommitmentStatus.ACTIVE:
                    if linked and not open_linked and any(t.status == TaskStatus.COMPLETED for t in linked):
                        cmt_td = cmt.target_date.date() if (cmt.target_date and isinstance(cmt.target_date, datetime)) else cmt.target_date
                        honored_commitments.append({
                            "id": cmt.id,
                            "title": cmt.title,
                            "target_date": cmt_td.isoformat() if cmt_td else None,
                            "completed_at": None,
                        })
                    else:
                        cmt_td = cmt.target_date.date() if (cmt.target_date and isinstance(cmt.target_date, datetime)) else cmt.target_date
                        if cmt_td and cmt_td <= end_d:
                            unfulfilled_commitments.append({
                                "id": cmt.id,
                                "title": cmt.title,
                                "target_date": cmt_td.isoformat(),
                                "open_tasks_count": len(open_linked),
                            })

        # Query reflection memories (Phase 6G evening reviews)
        memories: List[Dict[str, Any]] = []
        try:
            mem_items = await self.memory_manager.list_memories(user_id=user_id, limit=50)
            for m in mem_items:
                m_tags = m.tags or []
                if "horizon_review" in m_tags or "weekly_horizon" in m_tags:
                    if m.created_at:
                        m_d = m.created_at.astimezone(tz).date()
                        if start_d <= m_d <= end_d:
                            memories.append({
                                "id": m.id,
                                "content": m.content,
                                "created_at": m.created_at.isoformat(),
                                "tags": m.tags,
                            })
        except Exception as exc:
            logger.debug(f"Failed to query reflection memories: {exc}")

        # Velocity score (presentation only)
        total_work = len(completed_tasks) + len([t for t in all_tasks if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS) and t.due_at and t.due_at.astimezone(tz).date() <= end_d])
        velocity = (len(completed_tasks) / total_work * 100.0) if total_work > 0 else 0.0

        return WeeklyRetrospective(
            user_id=user_id,
            timezone=str(tz),
            lookback_start_date=start_d.isoformat(),
            lookback_end_date=end_d.isoformat(),
            completed_tasks_count=len(completed_tasks),
            completed_tasks=completed_tasks,
            honored_commitments_count=len(honored_commitments),
            honored_commitments=honored_commitments,
            unfulfilled_commitments_count=len(unfulfilled_commitments),
            unfulfilled_commitments=unfulfilled_commitments,
            recurring_rollover_tasks=recurring_rollover,
            reflection_memories=memories,
            velocity_score=round(velocity, 1),
            omitted_domains=omitted_domains,
        )

    # ── 5. Propose Weekly Retrospective ──────────────────────────────────────

    async def propose_weekly_retrospective(
        self,
        commitment_ids: Optional[List[str]] = None,
        retrospective_notes: Optional[str] = None,
        lookback_end_date: Optional[Union[date, str]] = None,
        explicit_tz: Optional[str] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> WeeklyRetrospectiveProposal:
        user_id = _enforce_authenticated_user_id(user_id)
        tz = await self.agenda_service.resolve_effective_timezone(explicit_tz, user_id=user_id)

        has_commitments = await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id)
        if not has_commitments and commitment_ids:
            raise PermissionError("Access denied: COMMITMENTS permission required to propose commitment completions.")

        operations: List[WeeklyOperation] = []
        order = 0
        all_tasks = await self.task_manager.list_tasks(user_id=user_id, limit=200)

        commitment_ids = commitment_ids or []
        for cid in commitment_ids:
            cmt = await self.commitment_manager.get_commitment(commitment_id=cid, user_id=user_id)
            if not cmt:
                raise ValueError(f"Commitment '{cid}' not found or access denied.")

            # COMPLETION_ELIGIBLE check: 100% of linked tasks must be COMPLETED
            linked = [t for t in all_tasks if t.source_commitment_id == cmt.id]
            if cmt.status != CommitmentStatus.ACTIVE:
                raise PreconditionFailedError(f"Commitment '{cid}' is {cmt.status.value}, not ACTIVE.")

            if linked:
                open_linked = [t for t in linked if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]
                if open_linked:
                    raise PreconditionFailedError(f"Cannot complete commitment '{cid}': {len(open_linked)} linked task(s) remain open.")
                completed_linked = [t for t in linked if t.status == TaskStatus.COMPLETED]
                if not completed_linked:
                    raise PreconditionFailedError(f"Cannot complete commitment '{cid}': all linked tasks were cancelled.")

            precond = WeeklyCommitmentCompletePrecondition(
                commitment_id=cmt.id,
                expected_status=cmt.status,
                linked_task_ids=[t.id for t in linked],
                expected_completed_task_ids=[t.id for t in linked if t.status == TaskStatus.COMPLETED],
            )

            dep_state = {
                "commitment": {
                    "id": cmt.id,
                    "status": cmt.status.value,
                    "updated_at": cmt.updated_at.isoformat() if cmt.updated_at else None,
                },
                "linked_tasks": [extract_canonical_task_payload(t) for t in linked],
            }

            operations.append(
                WeeklyOperation(
                    operation_id=f"op_{order:02d}_complete_{cmt.id}",
                    operation_type=WeeklyOperationType.COMPLETE_HONORED_COMMITMENT,
                    execution_order=order,
                    required_permissions=[PermissionType.COMMITMENTS],
                    precondition=precond,
                    dependency_entities=[{"type": "commitment", "id": cmt.id}] + [{"type": "task", "id": t.id} for t in linked],
                    dependency_state_snapshot=dep_state,
                )
            )
            order += 1

        # Strategic Memory (Option A)
        if retrospective_notes and retrospective_notes.strip():
            mem_precond = WeeklyMemoryCreatePrecondition(
                content=retrospective_notes.strip(),
                importance=3,
                confidence=1.0,
                source=MemorySource.USER_EXPLICIT,
                status=MemoryStatus.ACTIVE,
                tags=["weekly_horizon", "strategic_retrospective"],
            )
            operations.append(
                WeeklyOperation(
                    operation_id=f"op_{order:02d}_mem_{uuid.uuid4().hex[:6]}",
                    operation_type=WeeklyOperationType.CREATE_STRATEGIC_MEMORY,
                    execution_order=order,
                    required_permissions=[],
                    precondition=mem_precond,
                    dependency_entities=[],
                    dependency_state_snapshot={},
                )
            )
            order += 1

        union_permissions: Set[PermissionType] = set()
        for op in operations:
            union_permissions.update(op.required_permissions)

        state_hash = self._compute_proposal_state_hash(operations)
        now = datetime.now(timezone.utc)
        proposal_id = f"wpr_{uuid.uuid4().hex[:12]}"
        proposal = WeeklyRetrospectiveProposal(
            proposal_id=proposal_id,
            user_id=user_id,
            proposal_type=WeeklyProposalType.WEEKLY_RETROSPECTIVE,
            status=WeeklyProposalStatus.DRAFT,
            created_at=now,
            expires_at=now + timedelta(seconds=WEEKLY_PROPOSAL_TTL_SECONDS),
            state_hash=state_hash,
            required_permissions=sorted(list(union_permissions), key=lambda p: p.value),
            operations=operations,
            metadata={"timezone": str(tz)},
        )

        self._proposals[proposal_id] = proposal
        return proposal

    # ── 6. Apply Weekly Retrospective ────────────────────────────────────────

    async def apply_weekly_retrospective(
        self,
        proposal_id: str,
        approved: bool = True,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> WeeklyRetrospectiveProposal:
        user_id = _enforce_authenticated_user_id(user_id)
        proposal = self._proposals.get(proposal_id)
        if not proposal or proposal.user_id != user_id:
            raise ValueError("Weekly retrospective proposal not found or access denied.")

        if not isinstance(proposal, WeeklyRetrospectiveProposal):
            raise ValueError(f"Proposal '{proposal_id}' is not a WeeklyRetrospectiveProposal.")

        async with self._get_lock(proposal_id):
            now_utc = datetime.now(timezone.utc)
            if now_utc >= proposal.expires_at:
                proposal.status = WeeklyProposalStatus.EXPIRED
                raise PreconditionFailedError(f"Proposal '{proposal_id}' has expired.")

            if not approved:
                validate_weekly_state_transition(proposal.status, WeeklyProposalStatus.ABORTED)
                proposal.status = WeeklyProposalStatus.ABORTED
                return proposal

            if proposal.status == WeeklyProposalStatus.DRAFT:
                validate_weekly_state_transition(proposal.status, WeeklyProposalStatus.APPROVED)
                proposal.status = WeeklyProposalStatus.APPROVED

            validate_weekly_state_transition(proposal.status, WeeklyProposalStatus.EXECUTING)
            proposal.status = WeeklyProposalStatus.EXECUTING

            # Recheck permissions
            for perm in proposal.required_permissions:
                if not await self.permission_manager.is_authorized(perm, user_id=user_id):
                    proposal.status = WeeklyProposalStatus.FAILED
                    raise PreconditionFailedError(f"PERMISSION_REVOKED: Permission '{perm.value}' revoked.")

            executed_ops = 0
            for op in sorted(proposal.operations, key=lambda o: o.execution_order):
                try:
                    if op.operation_type == WeeklyOperationType.COMPLETE_HONORED_COMMITMENT:
                        assert isinstance(op.precondition, WeeklyCommitmentCompletePrecondition)
                        c_pre = op.precondition

                        # Revalidate commitment and linked tasks
                        cmt = await self.commitment_manager.get_commitment(commitment_id=c_pre.commitment_id, user_id=user_id)
                        if not cmt or cmt.status != CommitmentStatus.ACTIVE:
                            raise PreconditionFailedError(f"STATE_HASH_MISMATCH: Commitment '{c_pre.commitment_id}' not active.")

                        all_tasks = await self.task_manager.list_tasks(user_id=user_id, limit=200)
                        linked = [t for t in all_tasks if t.source_commitment_id == cmt.id]
                        curr_linked_ids = sorted([t.id for t in linked])
                        if curr_linked_ids != sorted(c_pre.linked_task_ids):
                            raise PreconditionFailedError("STATE_HASH_MISMATCH: Linked task set has changed.")

                        open_linked = [t for t in linked if t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]
                        if open_linked:
                            raise PreconditionFailedError(f"STATE_HASH_MISMATCH: Linked task(s) remain open.")

                        res = await self.commitment_manager.complete_commitment(commitment_id=cmt.id, user_id=user_id)
                        op.created_entity_id = res.id if res else cmt.id

                    elif op.operation_type == WeeklyOperationType.CREATE_STRATEGIC_MEMORY:
                        assert isinstance(op.precondition, WeeklyMemoryCreatePrecondition)
                        m_pre = op.precondition
                        mem_item, _ = await self.memory_manager.create_memory(
                            content=m_pre.content,
                            type=MemoryType.EPISODIC,
                            importance=m_pre.importance,
                            confidence=m_pre.confidence,
                            source=MemorySource.USER_EXPLICIT,
                            status=MemoryStatus.ACTIVE,
                            tags=m_pre.tags,
                            user_id=user_id,
                        )
                        op.created_entity_id = mem_item.id

                    op.status = WeeklyOperationStatus.COMPLETED
                    executed_ops += 1

                except Exception as exc:
                    logger.error(f"Retrospective operation {op.operation_id} failed: {exc}", exc_info=True)
                    op.status = WeeklyOperationStatus.FAILED
                    op.error_message = str(exc)

                    for subsequent in proposal.operations:
                        if subsequent.execution_order > op.execution_order:
                            subsequent.status = WeeklyOperationStatus.SKIPPED

                    if executed_ops == 0:
                        proposal.status = WeeklyProposalStatus.FAILED
                    else:
                        proposal.status = WeeklyProposalStatus.PARTIALLY_EXECUTED

                    if isinstance(exc, (PreconditionFailedError, WeeklyStateTransitionError)):
                        raise exc
                    raise PreconditionFailedError(f"Operation '{op.operation_id}' failed: {exc}") from exc

            proposal.status = WeeklyProposalStatus.EXECUTED
            return proposal

    # ── Proposal Management ──────────────────────────────────────────────────

    async def get_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[Union[WeeklyPlanProposal, WeeklyRetrospectiveProposal]]:
        user_id = _enforce_authenticated_user_id(user_id)
        proposal = self._proposals.get(proposal_id)
        if not proposal or proposal.user_id != user_id:
            return None
        # On-access expiry check
        if datetime.now(timezone.utc) >= proposal.expires_at and proposal.status in (WeeklyProposalStatus.DRAFT, WeeklyProposalStatus.APPROVED):
            proposal.status = WeeklyProposalStatus.EXPIRED
        return proposal

    async def abort_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Union[WeeklyPlanProposal, WeeklyRetrospectiveProposal]:
        user_id = _enforce_authenticated_user_id(user_id)
        proposal = self._proposals.get(proposal_id)
        if not proposal or proposal.user_id != user_id:
            raise ValueError("Proposal not found or access denied.")

        async with self._get_lock(proposal_id):
            validate_weekly_state_transition(proposal.status, WeeklyProposalStatus.ABORTED)
            proposal.status = WeeklyProposalStatus.ABORTED
            return proposal


# ── Dependency Injection Singletons ──────────────────────────────────────────

_weekly_horizon_service: Optional[ExecutiveWeeklyHorizonService] = None


def get_weekly_horizon_service() -> ExecutiveWeeklyHorizonService:
    global _weekly_horizon_service
    if _weekly_horizon_service is None:
        _weekly_horizon_service = ExecutiveWeeklyHorizonService()
    return _weekly_horizon_service


def get_weekly_horizon_service_dependency() -> ExecutiveWeeklyHorizonService:
    return get_weekly_horizon_service()
