from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
import zoneinfo
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.calendar.manager import CalendarManager, get_calendar_manager
from app.services.calendar.models import CalendarEvent
from app.services.tasks.manager import TaskManager
from app.services.tasks import get_task_manager
from app.services.tasks.models import Task, TaskStatus
from app.services.commitments.manager import CommitmentManager
from app.services.commitments import get_commitment_manager
from app.services.commitments.models import Commitment, CommitmentStatus
from app.services.reminders.manager import ReminderManager
from app.services.reminders import get_reminder_manager
from app.services.reminders.models import Reminder, ReminderStatus
from app.services.tools.base import ToolRiskLevel

from app.services.agenda.models import (
    AgendaConflict,
    ConflictType,
    ConflictSeverity,
    FreeSlot,
    DailyAgendaResponse,
)
from app.services.agenda.service import AgendaService, get_agenda_service, InvalidTimezoneError
from app.services.agenda.coordination_models import (
    StrategyCategory,
    ProposalStatus,
    ProposalFailureReason,
    CoordinationOperationType,
    OperationExecutionStatus,
    CalendarEventExecutionPrecondition,
    TaskUpdateExecutionPrecondition,
    ReminderCreateExecutionPrecondition,
    OperationExecutionPrecondition,
    DisplayOperationPreview,
    StrategyOption,
    AnalysisResult,
    CalendarSnapshotProvenance,
    ProposedOperation,
    SelectedCoordinationProposal,
    AllocatedTaskSlot,
    CoordinateScheduleResponse,
)

logger = logging.getLogger("aurora.services.agenda.coordination")

PROPOSAL_TTL_SECONDS = 900  # 15 minutes
ANALYSIS_TTL_SECONDS = 900  # 15 minutes


# ── Duration Resolution Contract ──────────────────────────────────────────────

def resolve_task_duration(task: Task, default_duration_minutes: int) -> int:
    """
    Deterministic Task Duration Precedence & Invalidation Contract.
    Identical algorithm at planning and execution.

    1. Validate request.default_duration_minutes >= 15.
    2. Check if the key "duration_minutes" exists in task.metadata:
       a. If absent: use default_duration_minutes.
       b. If present:
          - MUST be an integer AND >= 15.
          - If invalid: FAIL CLOSED immediately. NEVER silently fall back.
          - If valid: return task.metadata["duration_minutes"].
    """
    if default_duration_minutes < 15:
        raise ValueError(f"default_duration_minutes ({default_duration_minutes}) must be >= 15")

    metadata = getattr(task, "metadata", None)
    if metadata is not None and isinstance(metadata, dict) and "duration_minutes" in metadata:
        val = metadata["duration_minutes"]
        # In Python, bool is a subclass of int. Ensure val is strictly int and not bool.
        if isinstance(val, bool) or not isinstance(val, int):
            raise ValueError(f"Invalid duration_minutes in task metadata: {val!r}. Must be integer >= 15.")
        if val < 15:
            raise ValueError(f"duration_minutes in task metadata must be >= 15, got {val}.")
        return val

    return default_duration_minutes


# ── Canonical Calendar Event Fingerprinting ───────────────────────────────────

def compute_calendar_event_fingerprint(event: CalendarEvent) -> str:
    """
    Computes deterministic SHA-256 fingerprint for a referenced CalendarEvent.
    Payload:
    {
      "all_day": bool,
      "end_at": ISO8601 UTC string,
      "event_id": str,
      "start_at": ISO8601 UTC string
    }
    """
    canonical_dict = {
        "all_day": bool(event.all_day),
        "end_at": event.end_at.astimezone(timezone.utc).isoformat(),
        "event_id": str(event.event_id),
        "start_at": event.start_at.astimezone(timezone.utc).isoformat(),
    }
    canonical_json = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ── Global Non-Calendar State Hash ────────────────────────────────────────────

def compute_non_calendar_state_hash(
    tasks: List[Task],
    reminders: List[Reminder],
    commitments: List[Commitment],
) -> str:
    """
    Computes deterministic SHA-256 hash across NON-CALENDAR authoritative inputs only.
    Strictly excludes calendar event facts.
    """
    unique_tasks = list({str(t.task_id): t for t in tasks}.values())
    unique_reminders = list({str(r.reminder_id): r for r in reminders}.values())
    unique_commitments = list({str(c.commitment_id): c for c in commitments}.values())

    serialized_tasks = []
    for t in sorted(unique_tasks, key=lambda x: str(x.task_id)):
        serialized_tasks.append({
            "due_at": t.due_at.astimezone(timezone.utc).isoformat() if t.due_at else None,
            "id": str(t.task_id),
            "source_commitment_id": str(t.source_commitment_id) if getattr(t, "source_commitment_id", None) else None,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "title": str(t.title),
        })

    serialized_reminders = []
    for r in sorted(unique_reminders, key=lambda x: str(x.reminder_id)):
        serialized_reminders.append({
            "id": str(r.reminder_id),
            "scheduled_at": r.scheduled_at.astimezone(timezone.utc).isoformat() if r.scheduled_at else None,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "title": str(r.title),
        })

    serialized_commitments = []
    for c in sorted(unique_commitments, key=lambda x: str(x.commitment_id)):
        serialized_commitments.append({
            "id": str(c.commitment_id),
            "status": c.status.value if hasattr(c.status, "value") else str(c.status),
            "target_date": c.target_date.isoformat() if c.target_date else None,
        })

    payload = {
        "commitments": serialized_commitments,
        "reminders": serialized_reminders,
        "tasks": serialized_tasks,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ── Schedule Coordination Service ─────────────────────────────────────────────

class ScheduleCoordinationService:
    """
    Orchestration layer governing multi-task schedule coordination, conflict resolution
    planning, proposal lifecycle, and two-tier execution validation.
    Zero direct domain mutation authority: all mutations dispatch through existing
    authoritative managers / tools.
    """

    def __init__(
        self,
        agenda_service: Optional[AgendaService] = None,
        calendar_manager: Optional[CalendarManager] = None,
        task_manager: Optional[TaskManager] = None,
        commitment_manager: Optional[CommitmentManager] = None,
        reminder_manager: Optional[ReminderManager] = None,
        permission_manager: Optional[PermissionManager] = None,
    ):
        self._agenda_service = agenda_service
        self._calendar_manager = calendar_manager
        self._task_manager = task_manager
        self._commitment_manager = commitment_manager
        self._reminder_manager = reminder_manager
        self._permission_manager = permission_manager

        # Ephemeral in-memory stores (per-process, zero DB persistence)
        self._analyses: Dict[str, AnalysisResult] = {}
        self._analysis_default_durations: Dict[str, int] = {}
        self._proposals: Dict[str, SelectedCoordinationProposal] = {}
        self._execution_locks: Dict[str, asyncio.Lock] = {}

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
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager or default_permission_manager

    def _get_execution_lock(self, proposal_id: str) -> asyncio.Lock:
        if proposal_id not in self._execution_locks:
            self._execution_locks[proposal_id] = asyncio.Lock()
        return self._execution_locks[proposal_id]

    # ── 1. Read-Only Schedule Coordination (Multi-Task Allocation) ────────────

    async def coordinate_schedule(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        target_date: Optional[date] = None,
        explicit_tz: Optional[str] = None,
        work_start_hour: int = 9,
        work_end_hour: int = 18,
        default_duration_minutes: int = 30,
        task_ids: Optional[List[str]] = None,
    ) -> CoordinateScheduleResponse:
        """
        Coordinates and sequences tasks across open working gaps on target_date.
        Read-only, produces zero database mutations, and allocates tasks ephemerally
        without changing task deadlines (allocation != deadline).
        """
        tz = await self.agenda_service.resolve_effective_timezone(explicit_tz, user_id=user_id)
        eval_date = target_date or datetime.now(tz).date()

        # Retrieve daily agenda free slots
        daily_agenda = await self.agenda_service.get_daily_agenda(
            user_id=user_id,
            target_date=eval_date,
            explicit_tz=str(tz),
            include_conflicts=True,
            include_free_slots=True,
            work_start_hour=work_start_hour,
            work_end_hour=work_end_hour,
        )

        # Retrieve tasks
        all_tasks = await self.task_manager.list_tasks(user_id=user_id)
        active_tasks = [t for t in all_tasks if t.status in [TaskStatus.PENDING, TaskStatus.IN_PROGRESS]]
        if task_ids:
            task_set = set(task_ids)
            active_tasks = [t for t in active_tasks if t.task_id in task_set]

        # Deterministic ordering of tasks: priority first, due_at second, task_id tiebreaker
        def task_sort_key(t: Task) -> Tuple[int, datetime, str]:
            priority_rank = {"high": 0, "medium": 1, "low": 2}.get(
                t.priority.value if hasattr(t.priority, "value") else str(t.priority or "medium").lower(), 1
            )
            due = t.due_at or datetime.max.replace(tzinfo=timezone.utc)
            return (priority_rank, due, t.task_id)

        active_tasks.sort(key=task_sort_key)

        # Copy available free gaps for allocation
        available_gaps: List[Dict[str, datetime]] = [
            {"start": slot.start_at, "end": slot.end_at}
            for slot in daily_agenda.free_slots
        ]
        if not available_gaps and any(s.get("reason") == "NO_SNAPSHOT_REPORTED" for s in daily_agenda.omitted_sources):
            day_start_local = datetime.combine(eval_date, time(work_start_hour, 0), tzinfo=tz)
            day_end_local = datetime.combine(eval_date, time(work_end_hour, 0), tzinfo=tz)
            available_gaps = [{"start": day_start_local.astimezone(timezone.utc), "end": day_end_local.astimezone(timezone.utc)}]

        allocations: List[AllocatedTaskSlot] = []
        unallocated: List[Dict[str, Any]] = []

        for task in active_tasks:
            try:
                duration_m = resolve_task_duration(task, default_duration_minutes)
            except ValueError as e:
                unallocated.append({
                    "task_id": task.task_id,
                    "task_title": task.title,
                    "reason": f"INVALID_DURATION: {str(e)}",
                })
                continue

            allocated = False
            for i, gap in enumerate(available_gaps):
                gap_duration_m = int((gap["end"] - gap["start"]).total_seconds() / 60)
                if gap_duration_m >= duration_m:
                    alloc_start = gap["start"]
                    alloc_end = alloc_start + timedelta(minutes=duration_m)
                    allocations.append(AllocatedTaskSlot(
                        task_id=task.task_id,
                        task_title=task.title,
                        allocated_start_at=alloc_start,
                        allocated_end_at=alloc_end,
                        duration_minutes=duration_m,
                        due_at=task.due_at,
                        status=task.status.value if hasattr(task.status, "value") else str(task.status),
                    ))
                    # Shrink or remove gap
                    if alloc_end < gap["end"]:
                        gap["start"] = alloc_end
                    else:
                        available_gaps.pop(i)
                    allocated = True
                    break

            if not allocated:
                unallocated.append({
                    "task_id": task.task_id,
                    "task_title": task.title,
                    "reason": f"INSUFFICIENT_WORKING_GAP (needs {duration_m}m)",
                })

        return CoordinateScheduleResponse(
            target_date=eval_date.isoformat(),
            timezone=str(tz),
            allocations=allocations,
            unallocated_tasks=unallocated,
            conflicts=daily_agenda.conflicts,
            notes=["Ephemeral allocations computed. Task deadlines were NOT modified."],
        )

    # ── 2. Read-Only Conflict & Strategy Analysis ─────────────────────────────

    async def analyze_conflicts_and_strategies(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        conversation_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        target_date: Optional[date] = None,
        explicit_tz: Optional[str] = None,
        window_hours: int = 24,
        default_duration_minutes: int = 30,
        work_start_hour: int = 9,
        work_end_hour: int = 18,
        now: Optional[datetime] = None,
        rescheduling_requested: bool = False,
    ) -> AnalysisResult:
        """
        Scans for conflicts and generates deterministic, non-executable StrategyOptions.
        Output is strictly read-only.
        """
        tz = await self.agenda_service.resolve_effective_timezone(explicit_tz, user_id=user_id)
        now_utc = now or datetime.now(timezone.utc)
        eval_date = target_date or now_utc.astimezone(tz).date()

        # Supersede any active analysis for this user/conversation
        for prior_analysis in self._analyses.values():
            if prior_analysis.user_id == user_id:
                if conversation_id and prior_analysis.conversation_id == conversation_id:
                    prior_analysis.is_superseded = True
                elif not conversation_id:
                    prior_analysis.is_superseded = True

        # Scan conflicts
        day_start_local = datetime.combine(eval_date, time(0, 0), tzinfo=tz)
        scan_start_utc = min(now_utc, day_start_local.astimezone(timezone.utc))
        conflict_scan = await self.agenda_service.detect_conflicts(
            user_id=user_id,
            window_hours=max(window_hours, 24),
            start_datetime=scan_start_utc,
            explicit_tz=str(tz),
            now=now_utc,
        )

        analysis_id = f"anl_{uuid.uuid4().hex[:12]}"
        strategy_options: List[StrategyOption] = []

        # Retrieve relevant entities
        all_tasks = await self.task_manager.list_tasks(user_id=user_id)
        task_lookup = {t.task_id: t for t in all_tasks}

        # Check calendar authorization & retrieve events
        has_calendar_perm = await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id)
        has_tasks_perm = await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
        has_reminders_perm = await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)

        calendar_events = []
        if has_calendar_perm:
            query_start = min(now_utc - timedelta(hours=2), scan_start_utc)
            query_end = max(now_utc + timedelta(hours=window_hours + 2), scan_start_utc + timedelta(hours=window_hours + 2))
            evs, reason = await self.calendar_manager.get_events(query_start, query_end, user_id=user_id)
            if evs:
                calendar_events = evs
        event_lookup = {e.event_id: e for e in calendar_events}

        for conflict in conflict_scan.conflicts:
            # 1. Deadline during event or task overlap
            if conflict.conflict_type in [ConflictType.DEADLINE_DURING_EVENT, ConflictType.EVENT_OVERLAP]:
                task_item_id = next((iid for iid in conflict.item_ids if iid.startswith("tasks:")), None)
                cal_item_id = next((iid for iid in conflict.item_ids if iid.startswith("calendar:")), None)

                if task_item_id and cal_item_id:
                    task_id = task_item_id.split(":", 1)[1]
                    event_id = cal_item_id.split(":", 1)[1]
                    task = task_lookup.get(task_id)
                    event = event_lookup.get(event_id)

                    if task and event:
                        # Attempt to resolve task duration
                        duration_valid = True
                        try:
                            effective_duration = resolve_task_duration(task, default_duration_minutes)
                        except ValueError as exc:
                            duration_valid = False
                            effective_duration = default_duration_minutes

                        # Retrieve linked commitment if any
                        linked_commitment: Optional[Commitment] = None
                        if getattr(task, "commitment_id", None):
                            linked_commitment = await self.commitment_manager.get_commitment(
                                task.commitment_id, user_id=user_id
                            )

                        # Working hours boundaries in UTC for target day
                        day_start_local = datetime.combine(event.start_at.astimezone(tz).date(), time(work_start_hour, 0), tzinfo=tz)
                        day_end_local = datetime.combine(event.start_at.astimezone(tz).date(), time(work_end_hour, 0), tzinfo=tz)
                        day_start_utc = day_start_local.astimezone(timezone.utc)
                        day_end_utc = day_end_local.astimezone(timezone.utc)

                        # Strategy A: MOVE_DEADLINE_BEFORE_EVENT
                        strat_a_id = f"strat_{uuid.uuid4().hex[:8]}"
                        # Contiguous gap before event:
                        # G_start >= now_utc and >= day_start_utc
                        # G_end <= event.start_at.astimezone(timezone.utc) and <= day_end_utc
                        g_start_before = max(now_utc, day_start_utc)
                        g_end_before = min(event.start_at.astimezone(timezone.utc), day_end_utc)
                        gap_before_minutes = max(0, int((g_end_before - g_start_before).total_seconds() / 60))

                        # Check if another calendar event blocks this gap
                        for other_ev in calendar_events:
                            if other_ev.event_id != event.event_id and not other_ev.all_day:
                                o_start = other_ev.start_at.astimezone(timezone.utc)
                                o_end = other_ev.end_at.astimezone(timezone.utc)
                                if o_end > g_start_before and o_start < g_end_before:
                                    # Overlap reduces available gap
                                    if o_start <= g_start_before and o_end >= g_end_before:
                                        gap_before_minutes = 0
                                    elif o_start > g_start_before:
                                        gap_before_minutes = min(gap_before_minutes, int((o_start - g_start_before).total_seconds() / 60))

                        feasible_before = (
                            duration_valid
                            and gap_before_minutes >= effective_duration
                            and g_start_before < g_end_before
                            and g_start_before >= now_utc
                        )

                        missing_perms_a = []
                        if not has_tasks_perm:
                            missing_perms_a.append(PermissionType.TASKS.value)

                        # Proposed due_at before event:
                        proposed_due_before = g_start_before + timedelta(minutes=effective_duration)
                        preview_a = [
                            DisplayOperationPreview(
                                operation_type=CoordinationOperationType.UPDATE_TASK_DUE_DATE,
                                target_entity_id=task.task_id,
                                display_summary=f"Advance '{task.title}' deadline to {proposed_due_before.strftime('%H:%M')} (before '{event.title}')",
                                field_changes={
                                    "due_at": proposed_due_before.isoformat(),
                                    "effective_duration_minutes": effective_duration,
                                }
                            )
                        ]

                        strategy_options.append(StrategyOption(
                            strategy_id=strat_a_id,
                            analysis_id=analysis_id,
                            conflict_id=conflict.conflict_id,
                            category=StrategyCategory.RESOLUTION,
                            name=f"Advance Deadline Before '{event.title}'",
                            description=f"Move task deadline to {proposed_due_before.strftime('%H:%M')} into a verified {gap_before_minutes}m pre-meeting gap.",
                            effective_duration_minutes=effective_duration,
                            is_available=(feasible_before and len(missing_perms_a) == 0),
                            missing_permissions=missing_perms_a,
                            feasibility_proof=f"Proven contiguous gap: {g_start_before.strftime('%H:%M')}–{g_end_before.strftime('%H:%M')} ({gap_before_minutes}m available, {effective_duration}m required)",
                            preview_operations=preview_a,
                            tradeoffs="Requires completing the task prior to the scheduled meeting.",
                        ))

                        # Strategy B: MOVE_DEADLINE_AFTER_EVENT
                        strat_b_id = f"strat_{uuid.uuid4().hex[:8]}"
                        g_start_after = max(now_utc, event.end_at.astimezone(timezone.utc), day_start_utc)
                        g_end_after = day_end_utc
                        gap_after_minutes = max(0, int((g_end_after - g_start_after).total_seconds() / 60))

                        for other_ev in calendar_events:
                            if other_ev.event_id != event.event_id and not other_ev.all_day:
                                o_start = other_ev.start_at.astimezone(timezone.utc)
                                o_end = other_ev.end_at.astimezone(timezone.utc)
                                if o_end > g_start_after and o_start < g_end_after:
                                    if o_start <= g_start_after and o_end >= g_end_after:
                                        gap_after_minutes = 0
                                    elif o_start > g_start_after:
                                        gap_after_minutes = min(gap_after_minutes, int((o_start - g_start_after).total_seconds() / 60))

                        proposed_due_after = g_start_after + timedelta(minutes=effective_duration)

                        # Strict DATE_TARGET check against linked commitment:
                        date_target_valid = True
                        if linked_commitment and linked_commitment.target_date:
                            c_target = linked_commitment.target_date
                            if isinstance(c_target, datetime):
                                c_target = c_target.date()
                            elif isinstance(c_target, str):
                                c_target = date.fromisoformat(c_target[:10])
                            proposed_local_date = proposed_due_after.astimezone(tz).date()
                            if proposed_local_date > c_target:
                                date_target_valid = False

                        # Also ensure slot is strictly in the future:
                        future_valid = (g_start_after >= now_utc)

                        feasible_after = (
                            duration_valid
                            and gap_after_minutes >= effective_duration
                            and g_start_after < g_end_after
                            and future_valid
                            and date_target_valid
                        )

                        missing_perms_b = []
                        if not has_tasks_perm:
                            missing_perms_b.append(PermissionType.TASKS.value)

                        preview_b = [
                            DisplayOperationPreview(
                                operation_type=CoordinationOperationType.UPDATE_TASK_DUE_DATE,
                                target_entity_id=task.task_id,
                                display_summary=f"Postpone '{task.title}' deadline to {proposed_due_after.strftime('%H:%M')} (after '{event.title}')",
                                field_changes={
                                    "due_at": proposed_due_after.isoformat(),
                                    "effective_duration_minutes": effective_duration,
                                }
                            )
                        ]

                        strategy_options.append(StrategyOption(
                            strategy_id=strat_b_id,
                            analysis_id=analysis_id,
                            conflict_id=conflict.conflict_id,
                            category=StrategyCategory.RESOLUTION,
                            name=f"Postpone Deadline After '{event.title}'",
                            description=f"Move task deadline to {proposed_due_after.strftime('%H:%M')} into a verified {gap_after_minutes}m post-meeting gap.",
                            effective_duration_minutes=effective_duration,
                            is_available=(feasible_after and len(missing_perms_b) == 0),
                            missing_permissions=missing_perms_b,
                            feasibility_proof=f"Proven contiguous gap: {g_start_after.strftime('%H:%M')}–{g_end_after.strftime('%H:%M')} ({gap_after_minutes}m available, {effective_duration}m required)",
                            preview_operations=preview_b,
                            tradeoffs="Pushes task execution after the meeting.",
                        ))

                        # Strategy C: MITIGATION via Reminder
                        strat_c_id = f"strat_{uuid.uuid4().hex[:8]}"
                        proposed_reminder_time = max(now_utc, g_start_before)
                        missing_perms_c = []
                        if not has_reminders_perm:
                            missing_perms_c.append(PermissionType.REMINDERS.value)

                        preview_c = [
                            DisplayOperationPreview(
                                operation_type=CoordinationOperationType.CREATE_REMINDER,
                                target_entity_id=task.task_id,
                                display_summary=f"Create a preparation reminder for '{task.title}' at {proposed_reminder_time.strftime('%H:%M')}",
                                field_changes={
                                    "scheduled_at": proposed_reminder_time.isoformat(),
                                    "linked_task_id": task.task_id,
                                }
                            )
                        ]

                        strategy_options.append(StrategyOption(
                            strategy_id=strat_c_id,
                            analysis_id=analysis_id,
                            conflict_id=conflict.conflict_id,
                            category=StrategyCategory.MITIGATION,
                            name=f"Set Preparation Reminder for '{task.title}'",
                            description=f"Keep existing deadline, schedule a reminder at {proposed_reminder_time.strftime('%H:%M')}.",
                            effective_duration_minutes=effective_duration,
                            is_available=(duration_valid and len(missing_perms_c) == 0),
                            missing_permissions=missing_perms_c,
                            feasibility_proof=None,
                            preview_operations=preview_c,
                            tradeoffs="Does not resolve the deadline collision with the meeting.",
                        ))

            # 2. Overdue task
            elif conflict.conflict_type == ConflictType.TASK_OVERDUE:
                task_item_id = next((iid for iid in conflict.item_ids if iid.startswith("tasks:")), None)
                if task_item_id:
                    task_id = task_item_id.split(":", 1)[1]
                    task = task_lookup.get(task_id)
                    if task:
                        try:
                            effective_duration = resolve_task_duration(task, default_duration_minutes)
                            duration_valid = True
                        except ValueError:
                            duration_valid = False
                            effective_duration = default_duration_minutes

                        if not rescheduling_requested:
                            # Strategy: INFORMATION only
                            strat_info_id = f"strat_{uuid.uuid4().hex[:8]}"
                            strategy_options.append(StrategyOption(
                                strategy_id=strat_info_id,
                                analysis_id=analysis_id,
                                conflict_id=conflict.conflict_id,
                                category=StrategyCategory.INFORMATION,
                                name=f"Report Overdue Status for '{task.title}'",
                                description=f"Task was due at {task.due_at.strftime('%Y-%m-%d %H:%M') if task.due_at else 'unspecified'}. Zero modifications proposed.",
                                effective_duration_minutes=effective_duration,
                                is_available=True,
                                missing_permissions=[],
                                feasibility_proof=None,
                                preview_operations=[],
                                tradeoffs="No deadline change; reports status factually.",
                            ))
                        else:
                            # Rescheduling explicitly requested: find future working slot
                            strat_resched_id = f"strat_{uuid.uuid4().hex[:8]}"
                            # Calculate free slots on target date
                            today_local = eval_date
                            day_start_local = datetime.combine(today_local, time(work_start_hour, 0), tzinfo=tz)
                            day_end_local = datetime.combine(today_local, time(work_end_hour, 0), tzinfo=tz)
                            slot_start = max(now_utc, day_start_local.astimezone(timezone.utc))
                            slot_end = min(slot_start + timedelta(minutes=effective_duration), day_end_local.astimezone(timezone.utc))

                            feasible = (slot_start < day_end_local.astimezone(timezone.utc) and duration_valid)
                            preview_resched = [
                                DisplayOperationPreview(
                                    operation_type=CoordinationOperationType.UPDATE_TASK_DUE_DATE,
                                    target_entity_id=task.task_id,
                                    display_summary=f"Reschedule overdue task '{task.title}' to {slot_end.strftime('%H:%M')}",
                                    field_changes={"due_at": slot_end.isoformat()}
                                )
                            ]
                            strategy_options.append(StrategyOption(
                                strategy_id=strat_resched_id,
                                analysis_id=analysis_id,
                                conflict_id=conflict.conflict_id,
                                category=StrategyCategory.RESOLUTION,
                                name=f"Reschedule Overdue Task '{task.title}'",
                                description=f"Move deadline to next feasible working slot at {slot_end.strftime('%H:%M')}.",
                                effective_duration_minutes=effective_duration,
                                is_available=feasible,
                                missing_permissions=[] if has_tasks_perm else [PermissionType.TASKS.value],
                                feasibility_proof=f"Allocated in open working gap starting {slot_start.strftime('%H:%M')}",
                                preview_operations=preview_resched,
                                tradeoffs="Extends overdue task deadline to today's working hours.",
                            ))

        analysis = AnalysisResult(
            analysis_id=analysis_id,
            conversation_id=conversation_id,
            execution_id=execution_id,
            user_id=user_id,
            target_date=eval_date,
            timezone=str(tz),
            conflicts=conflict_scan.conflicts,
            strategy_options=strategy_options,
            created_at=now_utc,
            expires_at=now_utc + timedelta(seconds=ANALYSIS_TTL_SECONDS),
            is_superseded=False,
        )
        self._analyses[analysis_id] = analysis
        self._analysis_default_durations[analysis_id] = default_duration_minutes
        return analysis

    # ── 3. Strategy Selection & Proposal Construction ─────────────────────────

    async def create_proposal_from_strategy(
        self,
        analysis_id: str,
        strategy_id: str,
        execution_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        conversation_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> SelectedCoordinationProposal:
        """
        Creates an authoritative server-held SelectedCoordinationProposal from a
        user-selected strategy.
        Enforces:
        - Active, unexpired, non-superseded server-held AnalysisResult
        - Strategy belongs to analysis and is available
        - Constructs strictly typed OperationExecutionPreconditions
        - Immutably binds effective_duration_minutes
        - Computes non-calendar state_hash
        - Tracks calendar snapshot provenance
        """
        now_utc = now or datetime.now(timezone.utc)

        analysis = self._analyses.get(analysis_id)
        if not analysis:
            raise ValueError(f"Analysis '{analysis_id}' not found.")
        if analysis.user_id != user_id:
            raise PermissionError("User does not own this analysis.")
        if analysis.is_superseded:
            raise ValueError("Analysis has been superseded by a newer analysis.")
        if now_utc > analysis.expires_at:
            raise ValueError("ANALYSIS_EXPIRED")

        strategy = next((s for s in analysis.strategy_options if s.strategy_id == strategy_id), None)
        if not strategy:
            raise ValueError(f"Strategy '{strategy_id}' does not exist in analysis '{analysis_id}'.")
        if not strategy.is_available:
            raise ValueError("STRATEGY_UNAVAILABLE")

        proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        ordered_operations: List[ProposedOperation] = []

        referenced_tasks: List[Task] = []
        referenced_reminders: List[Reminder] = []
        referenced_commitments: List[Commitment] = []
        relevant_event_ids: List[str] = []

        # Retrieve referenced domain items to populate preconditions & state hash
        for preview_op in strategy.preview_operations:
            op_id = f"op_{uuid.uuid4().hex[:8]}"

            if preview_op.operation_type == CoordinationOperationType.UPDATE_TASK_DUE_DATE:
                task = await self.task_manager.get_task(preview_op.target_entity_id, user_id=user_id)
                if not task:
                    raise ValueError(f"Referenced task '{preview_op.target_entity_id}' not found.")
                referenced_tasks.append(task)

                # Linked commitment if any
                linked_comm: Optional[Commitment] = None
                if getattr(task, "commitment_id", None):
                    linked_comm = await self.commitment_manager.get_commitment(task.commitment_id, user_id=user_id)
                    if linked_comm:
                        referenced_commitments.append(linked_comm)

                # Find conflicting event from conflict_id
                conflict = next((c for c in analysis.conflicts if c.conflict_id == strategy.conflict_id), None)
                cal_preconditions: List[CalendarEventExecutionPrecondition] = []
                is_calendar_dependent = False

                if conflict:
                    for iid in conflict.item_ids:
                        if iid.startswith("calendar:"):
                            is_calendar_dependent = True
                            cal_id = iid.split(":", 1)[1]
                            relevant_event_ids.append(cal_id)
                            # Retrieve event from calendar manager
                            evs, _ = await self.calendar_manager.get_events(
                                start_at=now_utc - timedelta(days=2),
                                end_at=now_utc + timedelta(days=7),
                                user_id=user_id,
                            )
                            target_ev = next((e for e in (evs or []) if e.event_id == cal_id), None)
                            if target_ev:
                                cal_preconditions.append(CalendarEventExecutionPrecondition(
                                    event_id=target_ev.event_id,
                                    expected_start_at=target_ev.start_at.astimezone(timezone.utc),
                                    expected_end_at=target_ev.end_at.astimezone(timezone.utc),
                                    expected_all_day=target_ev.all_day,
                                    expected_fingerprint=compute_calendar_event_fingerprint(target_ev),
                                ))

                # Authoritative Task precondition
                task_precondition = TaskUpdateExecutionPrecondition(
                    task_id=task.task_id,
                    expected_version=task.updated_at.isoformat() if getattr(task, "updated_at", None) else getattr(task, "version", "1"),
                    expected_status=task.status.value if hasattr(task.status, "value") else str(task.status),
                    expected_due_at=task.due_at,
                    effective_duration_minutes=strategy.effective_duration_minutes,
                    linked_commitment_id=task.commitment_id,
                    expected_commitment_target_date=(
                        linked_comm.target_date.date()
                        if isinstance(linked_comm.target_date, datetime)
                        else linked_comm.target_date
                    ) if linked_comm else None,
                    expected_commitment_version=linked_comm.updated_at.isoformat() if linked_comm and getattr(linked_comm, "updated_at", None) else "1",
                    calendar_preconditions=cal_preconditions,
                )

                proposed_due_str = preview_op.field_changes.get("due_at")
                proposed_due = datetime.fromisoformat(proposed_due_str) if proposed_due_str else now_utc

                ordered_operations.append(ProposedOperation(
                    operation_id=op_id,
                    operation_type=CoordinationOperationType.UPDATE_TASK_DUE_DATE,
                    target_entity_id=task.task_id,
                    before_state={
                        "due_at": task.due_at.isoformat() if task.due_at else None,
                        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
                    },
                    proposed_state={"due_at": proposed_due.isoformat()},
                    required_permission=PermissionType.TASKS,
                    is_calendar_dependent=is_calendar_dependent,
                    precondition=OperationExecutionPrecondition(
                        operation_type=CoordinationOperationType.UPDATE_TASK_DUE_DATE,
                        task_update=task_precondition,
                    ),
                    risk_level=ToolRiskLevel.LOW,
                    status=OperationExecutionStatus.PENDING,
                    reason=f"Resolve schedule conflict for task '{task.title}'",
                ))

            elif preview_op.operation_type == CoordinationOperationType.CREATE_REMINDER:
                task = await self.task_manager.get_task(preview_op.target_entity_id, user_id=user_id)
                if task:
                    referenced_tasks.append(task)

                proposed_time_str = preview_op.field_changes.get("scheduled_at")
                proposed_time = datetime.fromisoformat(proposed_time_str) if proposed_time_str else now_utc

                reminder_precondition = ReminderCreateExecutionPrecondition(
                    linked_entity_type="task",
                    linked_entity_id=preview_op.target_entity_id,
                    expected_linked_version=task.updated_at.isoformat() if task and getattr(task, "updated_at", None) else "1",
                    expected_linked_status=task.status.value if task and hasattr(task.status, "value") else "pending",
                    proposed_scheduled_at=proposed_time,
                    calendar_preconditions=[],
                )

                ordered_operations.append(ProposedOperation(
                    operation_id=op_id,
                    operation_type=CoordinationOperationType.CREATE_REMINDER,
                    target_entity_id=preview_op.target_entity_id,
                    before_state={},
                    proposed_state={
                        "scheduled_at": proposed_time.isoformat(),
                        "linked_entity_type": "task",
                        "linked_entity_id": preview_op.target_entity_id,
                    },
                    required_permission=PermissionType.REMINDERS,
                    is_calendar_dependent=False,
                    precondition=OperationExecutionPrecondition(
                        operation_type=CoordinationOperationType.CREATE_REMINDER,
                        reminder_create=reminder_precondition,
                    ),
                    risk_level=ToolRiskLevel.LOW,
                    status=OperationExecutionStatus.PENDING,
                    reason=f"Set preparation reminder for task '{task.title if task else preview_op.target_entity_id}'",
                ))

        # Compute non-calendar global state_hash
        state_hash = compute_non_calendar_state_hash(
            tasks=referenced_tasks,
            reminders=referenced_reminders,
            commitments=referenced_commitments,
        )

        # Snapshot provenance
        snap_ts = self.calendar_manager.get_snapshot_timestamp(user_id=user_id) or now_utc
        provenance = CalendarSnapshotProvenance(
            snapshot_timestamp=snap_ts,
            relevant_event_ids=relevant_event_ids,
            snapshot_version=None,
        )

        # Required permissions
        req_perms = list({op.required_permission for op in ordered_operations})

        proposal = SelectedCoordinationProposal(
            proposal_id=proposal_id,
            analysis_id=analysis_id,
            execution_id=execution_id,
            selected_strategy_id=strategy_id,
            ordered_operations=ordered_operations,
            state_hash=state_hash,
            calendar_provenance=provenance,
            required_permissions=req_perms,
            risk_level=ToolRiskLevel.HIGH,  # Governed proposal requires composite confirmation!
            status=ProposalStatus.PENDING,
            failure_reason=None,
            expires_at=now_utc + timedelta(seconds=PROPOSAL_TTL_SECONDS),
            user_id=user_id,
            conversation_id=conversation_id,
            created_at=now_utc,
        )

        self._proposals[proposal_id] = proposal
        return proposal

    # ── 4. Two-Tier Governed Execution Engine ─────────────────────────────────

    async def execute_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        approved: bool = True,
        now: Optional[datetime] = None,
    ) -> SelectedCoordinationProposal:
        """
        Executes a SelectedCoordinationProposal following the approved Two-Tier validation model.

        Tier 1: Global Proposal Preflight Guards (0 operations execute on failure)
        1. Authenticate and validate ownership & TTL.
        2. Aggregate permission preflight across all proposal.required_permissions.
        3. If calendar-dependent: CalendarManager availability & freshness check (<=24h).
        4. Validate non-calendar global state_hash (covers referenced non-calendar items only).
        5. Transition proposal to EXECUTING.

        Tier 2: Per-Operation Dispatch Guards & Execution
        For each operation sequentially:
        1. Revalidate exact operation required_permission.
        2. If calendar-dependent:
           a. CalendarManager freshness check.
           b. Retrieve live events for CalendarEventExecutionPreconditions.
           c. Compare live facts against expected facts (start_at, end_at, all_day, fingerprint).
              Mismatch -> STALE_CALENDAR_STATE.
        3. Validate typed OperationExecutionPrecondition (status, version, live duration precedence).
        4. Dispatch authoritative domain tool/manager.
        5. Update operation status (COMPLETED or FAILED).
           If Op K fails: Op K=FAILED, K+1..N=ABORTED, Proposal=FAILED (if K==1) or PARTIALLY_EXECUTED (if K>1).
        """
        now_utc = now or datetime.now(timezone.utc)
        lock = self._get_execution_lock(proposal_id)

        async with lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{proposal_id}' not found.")

            if proposal.status in [ProposalStatus.COMPLETED, ProposalStatus.FAILED, ProposalStatus.CANCELLED, ProposalStatus.EXPIRED, ProposalStatus.PARTIALLY_EXECUTED]:
                raise ValueError(f"Proposal '{proposal_id}' is already in terminal state '{proposal.status.value}'.")

            # Cancellation check
            if not approved:
                proposal.status = ProposalStatus.CANCELLED
                for op in proposal.ordered_operations:
                    op.status = OperationExecutionStatus.ABORTED
                return proposal

            # ──────────────────────────────────────────────────────────────────
            # TIER 1: GLOBAL PROPOSAL PREFLIGHT GUARDS
            # ──────────────────────────────────────────────────────────────────

            # 1. Ownership & TTL
            if proposal.user_id != user_id:
                proposal.status = ProposalStatus.FAILED
                proposal.failure_reason = ProposalFailureReason.PERMISSION_REVOKED
                return proposal

            if now_utc > proposal.expires_at:
                proposal.status = ProposalStatus.EXPIRED
                return proposal

            # 2. Aggregate Permission Preflight
            for perm in proposal.required_permissions:
                is_auth = await self.permission_manager.is_authorized(perm, user_id=user_id)
                if not is_auth:
                    logger.warning("Preflight failed: Permission '%s' unauthorized for user '%s'", perm.value, user_id)
                    proposal.status = ProposalStatus.FAILED
                    proposal.failure_reason = ProposalFailureReason.PERMISSION_REVOKED
                    return proposal

            # 3. Calendar Snapshot Availability & Freshness Check (if any op is calendar-dependent)
            has_cal_dependent = any(op.is_calendar_dependent for op in proposal.ordered_operations)
            if has_cal_dependent:
                if not self.calendar_manager.is_snapshot_available(user_id=user_id):
                    logger.warning("Preflight failed: No calendar snapshot available for user '%s'", user_id)
                    proposal.status = ProposalStatus.FAILED
                    proposal.failure_reason = ProposalFailureReason.NO_CALENDAR_SNAPSHOT
                    return proposal

                if not self.calendar_manager.is_snapshot_fresh(user_id=user_id, max_age_seconds=86400, now=now_utc):
                    logger.warning("Preflight failed: Calendar snapshot is stale (>24h) for user '%s'", user_id)
                    proposal.status = ProposalStatus.FAILED
                    proposal.failure_reason = ProposalFailureReason.STALE_CALENDAR_SNAPSHOT
                    return proposal

            # 4. Non-Calendar Global State Hash Validation
            # Retrieve live state of referenced tasks, reminders, commitments
            live_tasks: List[Task] = []
            live_reminders: List[Reminder] = []
            live_commitments: List[Commitment] = []

            for op in proposal.ordered_operations:
                if op.operation_type == CoordinationOperationType.UPDATE_TASK_DUE_DATE:
                    t = await self.task_manager.get_task(op.target_entity_id, user_id=user_id)
                    if t:
                        live_tasks.append(t)
                        if getattr(t, "commitment_id", None):
                            c = await self.commitment_manager.get_commitment(t.commitment_id, user_id=user_id)
                            if c:
                                live_commitments.append(c)
                elif op.operation_type == CoordinationOperationType.CREATE_REMINDER:
                    # Target entity is linked task
                    if op.precondition.reminder_create and op.precondition.reminder_create.linked_entity_id:
                        t = await self.task_manager.get_task(op.precondition.reminder_create.linked_entity_id, user_id=user_id)
                        if t:
                            live_tasks.append(t)

            live_hash = compute_non_calendar_state_hash(live_tasks, live_reminders, live_commitments)
            if live_hash != proposal.state_hash:
                logger.warning(
                    "Preflight failed: Non-calendar state hash mismatch for proposal '%s' (expected %s, got %s)",
                    proposal.proposal_id, proposal.state_hash, live_hash
                )
                proposal.status = ProposalStatus.FAILED
                proposal.failure_reason = ProposalFailureReason.STALE_STATE_DETECTED
                return proposal

            # Tier 1 Passed: Transition to EXECUTING
            proposal.status = ProposalStatus.EXECUTING

            # ──────────────────────────────────────────────────────────────────
            # TIER 2: PER-OPERATION DISPATCH GUARDS & EXECUTION
            # ──────────────────────────────────────────────────────────────────

            for op_idx, op in enumerate(proposal.ordered_operations):
                op.status = OperationExecutionStatus.EXECUTING

                # Guard 2A: Revalidate exact operation required_permission
                op_perm_auth = await self.permission_manager.is_authorized(op.required_permission, user_id=user_id)
                if not op_perm_auth:
                    logger.warning("Operation %d failed: Permission '%s' revoked", op_idx + 1, op.required_permission.value)
                    op.status = OperationExecutionStatus.FAILED
                    op.failure_error = "PERMISSION_REVOKED"
                    self._abort_subsequent_operations(proposal, op_idx + 1)
                    proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                    proposal.failure_reason = ProposalFailureReason.PERMISSION_REVOKED
                    return proposal

                # Guard 2B: Calendar-Dependent Guards
                if op.is_calendar_dependent:
                    # 2B-1: Calendar Freshness & Availability
                    if not self.calendar_manager.is_snapshot_available(user_id=user_id):
                        op.status = OperationExecutionStatus.FAILED
                        op.failure_error = "NO_CALENDAR_SNAPSHOT"
                        self._abort_subsequent_operations(proposal, op_idx + 1)
                        proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                        proposal.failure_reason = ProposalFailureReason.NO_CALENDAR_SNAPSHOT
                        return proposal

                    if not self.calendar_manager.is_snapshot_fresh(user_id=user_id, max_age_seconds=86400, now=now_utc):
                        op.status = OperationExecutionStatus.FAILED
                        op.failure_error = "STALE_CALENDAR_SNAPSHOT"
                        self._abort_subsequent_operations(proposal, op_idx + 1)
                        proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                        proposal.failure_reason = ProposalFailureReason.STALE_CALENDAR_SNAPSHOT
                        return proposal

                    # 2B-2: Referenced Calendar Event Facts Integrity
                    cal_preconditions: List[CalendarEventExecutionPrecondition] = []
                    if op.precondition.task_update:
                        cal_preconditions = op.precondition.task_update.calendar_preconditions
                    elif op.precondition.reminder_create:
                        cal_preconditions = op.precondition.reminder_create.calendar_preconditions

                    for cal_prec in cal_preconditions:
                        live_events, _ = await self.calendar_manager.get_events(
                            start_at=cal_prec.expected_start_at - timedelta(hours=2),
                            end_at=cal_prec.expected_end_at + timedelta(hours=2),
                            user_id=user_id,
                        )
                        matching_ev = next((e for e in (live_events or []) if e.event_id == cal_prec.event_id), None)
                        if not matching_ev:
                            logger.warning("Referenced event '%s' missing at dispatch boundary", cal_prec.event_id)
                            op.status = OperationExecutionStatus.FAILED
                            op.failure_error = "STALE_CALENDAR_STATE"
                            self._abort_subsequent_operations(proposal, op_idx + 1)
                            proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                            proposal.failure_reason = ProposalFailureReason.STALE_CALENDAR_STATE
                            return proposal

                        live_fp = compute_calendar_event_fingerprint(matching_ev)
                        if (
                            matching_ev.start_at.astimezone(timezone.utc) != cal_prec.expected_start_at.astimezone(timezone.utc)
                            or matching_ev.end_at.astimezone(timezone.utc) != cal_prec.expected_end_at.astimezone(timezone.utc)
                            or matching_ev.all_day != cal_prec.expected_all_day
                            or live_fp != cal_prec.expected_fingerprint
                        ):
                            logger.warning("Referenced event '%s' state mutated at dispatch boundary", cal_prec.event_id)
                            op.status = OperationExecutionStatus.FAILED
                            op.failure_error = "STALE_CALENDAR_STATE"
                            self._abort_subsequent_operations(proposal, op_idx + 1)
                            proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                            proposal.failure_reason = ProposalFailureReason.STALE_CALENDAR_STATE
                            return proposal

                # Guard 2C: Typed OperationExecutionPrecondition Validation
                if op.operation_type == CoordinationOperationType.UPDATE_TASK_DUE_DATE:
                    prec = op.precondition.task_update
                    assert prec is not None
                    live_task = await self.task_manager.get_task(prec.task_id, user_id=user_id)
                    if not live_task:
                        op.status = OperationExecutionStatus.FAILED
                        op.failure_error = "OPERATION_PRECONDITION_FAILED"
                        self._abort_subsequent_operations(proposal, op_idx + 1)
                        proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                        proposal.failure_reason = ProposalFailureReason.OPERATION_PRECONDITION_FAILED
                        return proposal

                    # Check status
                    live_status_str = live_task.status.value if hasattr(live_task.status, "value") else str(live_task.status)
                    if live_status_str != prec.expected_status:
                        op.status = OperationExecutionStatus.FAILED
                        op.failure_error = "OPERATION_PRECONDITION_FAILED"
                        self._abort_subsequent_operations(proposal, op_idx + 1)
                        proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                        proposal.failure_reason = ProposalFailureReason.OPERATION_PRECONDITION_FAILED
                        return proposal

                    # Check due_at
                    if live_task.due_at != prec.expected_due_at:
                        op.status = OperationExecutionStatus.FAILED
                        op.failure_error = "OPERATION_PRECONDITION_FAILED"
                        self._abort_subsequent_operations(proposal, op_idx + 1)
                        proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                        proposal.failure_reason = ProposalFailureReason.OPERATION_PRECONDITION_FAILED
                        return proposal

                    # Re-resolve live duration using identical contract
                    planning_default = self._analysis_default_durations.get(proposal.analysis_id, 30)
                    try:
                        live_duration = resolve_task_duration(live_task, default_duration_minutes=planning_default)
                        if live_duration != prec.effective_duration_minutes:
                            logger.warning(
                                "Live resolved duration (%d) != bound effective_duration (%d)",
                                live_duration, prec.effective_duration_minutes
                            )
                            op.status = OperationExecutionStatus.FAILED
                            op.failure_error = "OPERATION_PRECONDITION_FAILED"
                            self._abort_subsequent_operations(proposal, op_idx + 1)
                            proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                            proposal.failure_reason = ProposalFailureReason.OPERATION_PRECONDITION_FAILED
                            return proposal
                    except ValueError:
                        # Present-but-invalid metadata fails closed
                        op.status = OperationExecutionStatus.FAILED
                        op.failure_error = "OPERATION_PRECONDITION_FAILED"
                        self._abort_subsequent_operations(proposal, op_idx + 1)
                        proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                        proposal.failure_reason = ProposalFailureReason.OPERATION_PRECONDITION_FAILED
                        return proposal

                    # Check linked commitment target_date
                    if prec.linked_commitment_id:
                        live_comm = await self.commitment_manager.get_commitment(prec.linked_commitment_id, user_id=user_id)
                        comm_target = live_comm.target_date if live_comm else None
                        if isinstance(comm_target, datetime):
                            comm_target = comm_target.date()
                        if not live_comm or comm_target != prec.expected_commitment_target_date:
                            op.status = OperationExecutionStatus.FAILED
                            op.failure_error = "OPERATION_PRECONDITION_FAILED"
                            self._abort_subsequent_operations(proposal, op_idx + 1)
                            proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                            proposal.failure_reason = ProposalFailureReason.OPERATION_PRECONDITION_FAILED
                            return proposal

                elif op.operation_type == CoordinationOperationType.CREATE_REMINDER:
                    prec_rem = op.precondition.reminder_create
                    assert prec_rem is not None
                    if prec_rem.linked_entity_type == "task" and prec_rem.linked_entity_id:
                        live_task = await self.task_manager.get_task(prec_rem.linked_entity_id, user_id=user_id)
                        if not live_task:
                            op.status = OperationExecutionStatus.FAILED
                            op.failure_error = "OPERATION_PRECONDITION_FAILED"
                            self._abort_subsequent_operations(proposal, op_idx + 1)
                            proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                            proposal.failure_reason = ProposalFailureReason.OPERATION_PRECONDITION_FAILED
                            return proposal

                # Guard Passed: Dispatch authoritative domain mutation!
                try:
                    if op.operation_type == CoordinationOperationType.UPDATE_TASK_DUE_DATE:
                        new_due_str = op.proposed_state["due_at"]
                        new_due = datetime.fromisoformat(new_due_str)
                        await self.task_manager.update_task(
                            task_id=op.target_entity_id,
                            due_at=new_due,
                            user_id=user_id,
                        )
                    elif op.operation_type == CoordinationOperationType.CREATE_REMINDER:
                        sched_str = op.proposed_state["scheduled_at"]
                        sched_dt = datetime.fromisoformat(sched_str)
                        linked_id = op.proposed_state.get("linked_entity_id")
                        await self.reminder_manager.create_reminder(
                            title=op.reason,
                            scheduled_at=sched_dt,
                            user_id=user_id,
                            task_id=linked_id,
                        )
                    op.status = OperationExecutionStatus.COMPLETED
                except Exception as exc:
                    logger.error("Domain tool execution raised exception on op %d: %s", op_idx + 1, exc, exc_info=True)
                    op.status = OperationExecutionStatus.FAILED
                    op.failure_error = str(exc) or "OPERATION_FAILED"
                    self._abort_subsequent_operations(proposal, op_idx + 1)
                    proposal.status = ProposalStatus.FAILED if op_idx == 0 else ProposalStatus.PARTIALLY_EXECUTED
                    proposal.failure_reason = ProposalFailureReason.OPERATION_FAILED
                    return proposal

            # All operations completed successfully!
            proposal.status = ProposalStatus.COMPLETED
            return proposal

    def _abort_subsequent_operations(self, proposal: SelectedCoordinationProposal, start_index: int) -> None:
        """Marks operations from start_index..N as ABORTED."""
        for op in proposal.ordered_operations[start_index:]:
            op.status = OperationExecutionStatus.ABORTED


# ── Dependency Provider ───────────────────────────────────────────────────────

_default_coordination_service: Optional[ScheduleCoordinationService] = None


def get_coordination_service(
    agenda_service: Optional[AgendaService] = None,
    calendar_manager: Optional[CalendarManager] = None,
    task_manager: Optional[TaskManager] = None,
    commitment_manager: Optional[CommitmentManager] = None,
    reminder_manager: Optional[ReminderManager] = None,
    permission_manager: Optional[PermissionManager] = None,
) -> ScheduleCoordinationService:
    global _default_coordination_service
    if any([agenda_service, calendar_manager, task_manager, commitment_manager, reminder_manager, permission_manager]):
        return ScheduleCoordinationService(
            agenda_service=agenda_service,
            calendar_manager=calendar_manager,
            task_manager=task_manager,
            commitment_manager=commitment_manager,
            reminder_manager=reminder_manager,
            permission_manager=permission_manager,
        )
    if _default_coordination_service is None:
        _default_coordination_service = ScheduleCoordinationService()
    return _default_coordination_service


def get_coordination_service_dependency() -> ScheduleCoordinationService:
    """FastAPI dependency provider."""
    return get_coordination_service()
