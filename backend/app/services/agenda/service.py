from __future__ import annotations

import logging
import zoneinfo
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.identity import default_identity_manager
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.calendar.manager import CalendarManager, get_calendar_manager
from app.services.calendar.models import CalendarEvent
from app.services.tasks.manager import TaskManager
from app.services.tasks import get_task_manager
from app.services.tasks.models import Task, TaskStatus, TaskPriority
from app.services.commitments.manager import CommitmentManager
from app.services.commitments import get_commitment_manager
from app.services.commitments.models import Commitment, CommitmentStatus, CommitmentPriority
from app.services.reminders.manager import ReminderManager
from app.services.reminders import get_reminder_manager
from app.services.reminders.models import Reminder, ReminderStatus
from app.services.agenda.models import (
    AgendaTemporalKind,
    AgendaItem,
    ConflictType,
    ConflictSeverity,
    AgendaConflict,
    FreeSlot,
    DailyAgendaResponse,
    ProposedSlotResponse,
    ConflictScanResponse,
)

logger = logging.getLogger("aurora.services.agenda")


class InvalidTimezoneError(ValueError):
    """Raised when an explicit IANA timezone identifier is invalid or unrecognized."""
    pass


class AgendaService:
    """
    Stateless, read-only orchestration and temporal projection service for Project Aurora.
    Normalizes temporal information across Calendar, Tasks, Commitments, and Reminders,
    computes deterministic feasible work slots, and evaluates cross-domain conflicts.
    Produces zero database mutations and emits zero client actions.
    """

    def __init__(
        self,
        calendar_manager: Optional[CalendarManager] = None,
        task_manager: Optional[TaskManager] = None,
        commitment_manager: Optional[CommitmentManager] = None,
        reminder_manager: Optional[ReminderManager] = None,
        permission_manager: Optional[PermissionManager] = None,
        identity_manager: Optional[Any] = None,
    ):
        self._calendar_manager = calendar_manager
        self._task_manager = task_manager
        self._commitment_manager = commitment_manager
        self._reminder_manager = reminder_manager
        self._permission_manager = permission_manager
        self._identity_manager = identity_manager

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

    @property
    def identity_manager(self) -> Any:
        return self._identity_manager or default_identity_manager

    # ── Timezone Resolution ───────────────────────────────────────────────────

    async def resolve_effective_timezone(
        self,
        explicit_tz: Optional[str] = None,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> zoneinfo.ZoneInfo:
        """
        Resolves the Effective Evaluation Timezone in priority order:
        1. Explicit user-requested IANA timezone supplied for that request.
        2. Authenticated user's configured timezone from profile metadata.
        3. UTC fallback.

        Explicit invalid/malformed IANA identifiers raise InvalidTimezoneError.
        """
        if explicit_tz is not None and explicit_tz.strip():
            tz_str = explicit_tz.strip()
            # Prohibit raw UTC offsets (e.g. +05:30, -04:00, UTC+5)
            if tz_str.startswith("+") or tz_str.startswith("-") or any(c in tz_str for c in ["+0", "-0", "+1", "-1"]):
                raise InvalidTimezoneError(f"Invalid IANA timezone identifier: '{tz_str}'. Raw offsets are not permitted.")
            try:
                return zoneinfo.ZoneInfo(tz_str)
            except Exception as exc:
                raise InvalidTimezoneError(f"Invalid IANA timezone identifier: '{tz_str}'.") from exc

        # Check authenticated user's configured preference
        try:
            user = await self.identity_manager.get_user(user_id)
            if user and hasattr(user, "metadata") and user.metadata:
                cfg_tz = user.metadata.get("timezone")
                if cfg_tz and isinstance(cfg_tz, str) and cfg_tz.strip():
                    return zoneinfo.ZoneInfo(cfg_tz.strip())
        except Exception:
            logger.debug("Could not resolve configured timezone for user '%s', falling back to UTC", user_id)

        return zoneinfo.ZoneInfo("UTC")

    # ── Daily Agenda Projection ──────────────────────────────────────────────

    async def get_daily_agenda(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        target_date: Optional[date] = None,
        explicit_tz: Optional[str] = None,
        include_conflicts: bool = True,
        include_free_slots: bool = True,
        work_start_hour: int = 9,
        work_end_hour: int = 18,
    ) -> DailyAgendaResponse:
        """
        Retrieves the unified chronological agenda for an explicit local date
        under the Effective Evaluation Timezone.
        """
        # Validate working hours range
        self._validate_working_hours(work_start_hour, work_end_hour)

        # Resolve Effective Evaluation Timezone
        effective_tz = await self.resolve_effective_timezone(explicit_tz=explicit_tz, user_id=user_id)

        # Default target_date to today in the Effective Evaluation Timezone
        if target_date is None:
            target_date = datetime.now(effective_tz).date()

        # Permission evaluation across all 4 domains
        perms = {
            "calendar": await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id),
            "tasks": await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id),
            "commitments": await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id),
            "reminders": await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id),
        }

        authorized_count = sum(1 for v in perms.values() if v)
        if authorized_count == 0:
            raise PermissionError("No personal agenda domains are authorized.")

        # Construct local start-of-day and end-of-day, converting to UTC
        local_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=effective_tz)
        local_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, 999999, tzinfo=effective_tz)
        window_start_utc = local_start.astimezone(timezone.utc)
        window_end_utc = local_end.astimezone(timezone.utc)

        omitted_sources: List[Dict[str, str]] = []
        raw_events: List[CalendarEvent] = []
        active_tasks: List[Task] = []
        active_commitments: List[Commitment] = []
        scheduled_reminders: List[Reminder] = []

        # 1. Calendar
        calendar_snapshot_exists = False
        if perms["calendar"]:
            if user_id not in self.calendar_manager._events:
                omitted_sources.append({"domain": "calendar", "reason": "NO_SNAPSHOT_REPORTED"})
            else:
                calendar_snapshot_exists = True
                evs, status = await self.calendar_manager.get_events(
                    start_at=window_start_utc,
                    end_at=window_end_utc,
                    max_results=50,
                    user_id=user_id,
                )
                if status == "available" and evs:
                    raw_events = evs
        else:
            omitted_sources.append({"domain": "calendar", "reason": "UNAUTHORIZED"})

        # 2. Tasks
        if perms["tasks"]:
            active_tasks = await self.task_manager.list_tasks(user_id=user_id, include_inactive=False, limit=200)
        else:
            omitted_sources.append({"domain": "tasks", "reason": "UNAUTHORIZED"})

        # 3. Commitments
        if perms["commitments"]:
            active_commitments = await self.commitment_manager.list_commitments(user_id=user_id, include_inactive=False, limit=200)
        else:
            omitted_sources.append({"domain": "commitments", "reason": "UNAUTHORIZED"})

        # 4. Reminders
        if perms["reminders"]:
            scheduled_reminders = await self.reminder_manager.list_reminders(user_id=user_id, statuses=[ReminderStatus.SCHEDULED])
        else:
            omitted_sources.append({"domain": "reminders", "reason": "UNAUTHORIZED"})

        # Project domain items into normalized AgendaItem instances
        header_items: List[AgendaItem] = []
        timed_items: List[AgendaItem] = []

        # Project Calendar events
        for ev in raw_events:
            item = AgendaItem(
                item_id=f"calendar:{ev.event_id}",
                source_domain="calendar",
                source_entity_id=ev.event_id,
                display_title=ev.title,
                temporal_kind=AgendaTemporalKind.INTERVAL,
                start_at=ev.start_at.astimezone(timezone.utc),
                end_at=ev.end_at.astimezone(timezone.utc),
                is_all_day=ev.all_day,
                status="scheduled",
            )
            if ev.all_day:
                header_items.append(item)
            else:
                timed_items.append(item)

        # Project Tasks due on this day
        for tsk in active_tasks:
            if tsk.due_at:
                due_utc = tsk.due_at.astimezone(timezone.utc)
                if window_start_utc <= due_utc <= window_end_utc:
                    timed_items.append(AgendaItem(
                        item_id=f"tasks:{tsk.id}",
                        source_domain="tasks",
                        source_entity_id=tsk.id,
                        display_title=tsk.title,
                        temporal_kind=AgendaTemporalKind.INSTANT,
                        instant_at=due_utc,
                        status=tsk.status.value if hasattr(tsk.status, "value") else str(tsk.status),
                        priority=tsk.priority.value if hasattr(tsk.priority, "value") else str(tsk.priority),
                        source_commitment_id=tsk.source_commitment_id,
                    ))

        # Project Reminders firing on this day
        for rem in scheduled_reminders:
            sched_utc = rem.scheduled_at.astimezone(timezone.utc)
            if window_start_utc <= sched_utc <= window_end_utc:
                timed_items.append(AgendaItem(
                    item_id=f"reminders:{rem.id}",
                    source_domain="reminders",
                    source_entity_id=rem.id,
                    display_title=rem.title,
                    temporal_kind=AgendaTemporalKind.INSTANT,
                    instant_at=sched_utc,
                    status=rem.status.value if hasattr(rem.status, "value") else str(rem.status),
                ))

        # Project Commitments whose target date matches target_date
        for cmt in active_commitments:
            if cmt.target_date:
                cmt_date = cmt.target_date.astimezone(effective_tz).date()
                if cmt_date == target_date:
                    header_items.append(AgendaItem(
                        item_id=f"commitments:{cmt.id}",
                        source_domain="commitments",
                        source_entity_id=cmt.id,
                        display_title=cmt.title,
                        temporal_kind=AgendaTemporalKind.DATE_TARGET,
                        target_date=cmt_date,
                        status=cmt.status.value if hasattr(cmt.status, "value") else str(cmt.status),
                        priority=cmt.priority.value if hasattr(cmt.priority, "value") else str(cmt.priority),
                    ))

        # Evaluate Conflicts across active items
        conflicts: List[AgendaConflict] = []
        if include_conflicts:
            now_utc = datetime.now(timezone.utc)
            conflicts = self._evaluate_conflicts(
                events=raw_events,
                tasks=active_tasks,
                reminders=scheduled_reminders,
                commitments=active_commitments,
                window_start_utc=window_start_utc,
                window_end_utc=window_end_utc,
                effective_tz=effective_tz,
                now_utc=now_utc,
            )

            # Map conflict_ids onto items
            conflict_map: Dict[str, List[str]] = {}
            for conf in conflicts:
                for item_id in conf.item_ids:
                    conflict_map.setdefault(item_id, []).append(conf.conflict_id)

            for item in header_items + timed_items:
                if item.item_id in conflict_map:
                    item.conflict_ids = conflict_map[item.item_id]

        # Calculate Free Slots if requested and calendar is available
        free_slots: List[FreeSlot] = []
        if include_free_slots and perms["calendar"] and calendar_snapshot_exists:
            free_slots = self._calculate_free_slots_for_day(
                target_date=target_date,
                events=raw_events,
                effective_tz=effective_tz,
                work_start_hour=work_start_hour,
                work_end_hour=work_end_hour,
            )

        # Deterministic sorting:
        # Header items: DATE_TARGET items first (priority desc, title asc, item_id asc), then all-day intervals
        date_target_items = [it for it in header_items if it.temporal_kind == AgendaTemporalKind.DATE_TARGET]
        date_target_items.sort(key=lambda it: (
            -self._priority_rank(it.priority),
            it.display_title.lower(),
            it.item_id
        ))

        all_day_intervals = [it for it in header_items if it.temporal_kind == AgendaTemporalKind.INTERVAL]
        all_day_intervals.sort(key=lambda it: (it.start_at, it.item_id))

        # Timed items: chronological ascending, item_id tie-breaker
        timed_items.sort(key=lambda it: (
            it.start_at or it.instant_at or datetime.max.replace(tzinfo=timezone.utc),
            it.item_id
        ))

        combined_items = date_target_items + all_day_intervals + timed_items

        return DailyAgendaResponse(
            target_date=target_date.isoformat(),
            timezone=str(effective_tz),
            items=combined_items,
            conflicts=conflicts,
            free_slots=free_slots,
            omitted_sources=omitted_sources,
        )

    # ── Conflict Detection Engine ─────────────────────────────────────────────

    async def detect_conflicts(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        window_hours: int = 24,
        start_datetime: Optional[datetime] = None,
        explicit_tz: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> ConflictScanResponse:
        """
        Scans a sliding temporal window [start_datetime, start_datetime + window_hours]
        for cross-domain collisions and domain logic inconsistencies.
        Reuses the single conflict engine without code duplication.
        """
        if window_hours < 1 or window_hours > 168:
            raise ValueError("window_hours must be between 1 and 168 (1 hour to 7 days).")

        effective_tz = await self.resolve_effective_timezone(explicit_tz=explicit_tz, user_id=user_id)

        if start_datetime is not None:
            if start_datetime.tzinfo is None:
                raise ValueError("start_datetime must be timezone-aware.")
            start_utc = start_datetime.astimezone(timezone.utc)
        else:
            start_utc = datetime.now(timezone.utc)

        if now is not None:
            now_utc = now.astimezone(timezone.utc)
        elif start_datetime is not None:
            now_utc = start_datetime.astimezone(timezone.utc)
        else:
            now_utc = datetime.now(timezone.utc)

        end_utc = start_utc + timedelta(hours=window_hours)

        perms = {
            "calendar": await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id),
            "tasks": await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id),
            "commitments": await self.permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id),
            "reminders": await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id),
        }

        authorized_count = sum(1 for v in perms.values() if v)
        if authorized_count == 0:
            raise PermissionError("No personal agenda domains are authorized.")

        omitted_domains: List[str] = [domain for domain, allowed in perms.items() if not allowed]

        raw_events: List[CalendarEvent] = []
        if perms["calendar"]:
            if user_id not in self.calendar_manager._events:
                omitted_domains.append("calendar (no snapshot)")
            else:
                evs, status = await self.calendar_manager.get_events(
                    start_at=start_utc,
                    end_at=end_utc,
                    max_results=50,
                    user_id=user_id,
                )
                if status == "available" and evs:
                    raw_events = evs

        active_tasks: List[Task] = []
        if perms["tasks"]:
            active_tasks = await self.task_manager.list_tasks(user_id=user_id, include_inactive=False, limit=200)

        active_commitments: List[Commitment] = []
        if perms["commitments"]:
            active_commitments = await self.commitment_manager.list_commitments(user_id=user_id, include_inactive=False, limit=200)

        scheduled_reminders: List[Reminder] = []
        if perms["reminders"]:
            scheduled_reminders = await self.reminder_manager.list_reminders(user_id=user_id, statuses=[ReminderStatus.SCHEDULED])

        conflicts = self._evaluate_conflicts(
            events=raw_events,
            tasks=active_tasks,
            reminders=scheduled_reminders,
            commitments=active_commitments,
            window_start_utc=start_utc,
            window_end_utc=end_utc,
            effective_tz=effective_tz,
            now_utc=now_utc,
        )

        return ConflictScanResponse(
            conflicts=conflicts,
            total_conflicts=len(conflicts),
            evaluated_window_start=start_utc,
            evaluated_window_end=end_utc,
            omitted_domains=omitted_domains,
        )

    def _evaluate_conflicts(
        self,
        events: List[CalendarEvent],
        tasks: List[Task],
        reminders: List[Reminder],
        commitments: List[Commitment],
        window_start_utc: datetime,
        window_end_utc: datetime,
        effective_tz: zoneinfo.ZoneInfo,
        now_utc: datetime,
    ) -> List[AgendaConflict]:
        """
        Pure, deterministic conflict evaluation over domain entity collections.
        """
        conflicts: List[AgendaConflict] = []
        non_all_day_events = [e for e in events if not e.all_day]

        # A. EVENT_OVERLAP: Non-all-day CalendarEvent vs CalendarEvent
        for i in range(len(non_all_day_events)):
            for j in range(i + 1, len(non_all_day_events)):
                ev1 = non_all_day_events[i]
                ev2 = non_all_day_events[j]
                ev1_start = ev1.start_at.astimezone(timezone.utc)
                ev1_end = ev1.end_at.astimezone(timezone.utc)
                ev2_start = ev2.start_at.astimezone(timezone.utc)
                ev2_end = ev2.end_at.astimezone(timezone.utc)

                overlap_start = max(ev1_start, ev2_start)
                overlap_end = min(ev1_end, ev2_end)

                if overlap_start < overlap_end:
                    overlap_minutes = int((overlap_end - overlap_start).total_seconds() / 60)
                    if overlap_minutes > 0:
                        pair = sorted([ev1.event_id, ev2.event_id])
                        conflicts.append(AgendaConflict(
                            conflict_id=f"conf_event_overlap_{pair[0]}_{pair[1]}",
                            conflict_type=ConflictType.EVENT_OVERLAP,
                            severity=ConflictSeverity.INFO,
                            description=f"Calendar events '{ev1.title}' and '{ev2.title}' overlap by {overlap_minutes} minutes.",
                            item_ids=[f"calendar:{ev1.event_id}", f"calendar:{ev2.event_id}"],
                            detected_at=now_utc,
                            suggested_action="Review overlapping meetings for attendance necessity.",
                        ))

        # B. DEADLINE_DURING_EVENT: Active Task deadline falling inside a non-all-day event
        for tsk in tasks:
            if tsk.due_at:
                due_utc = tsk.due_at.astimezone(timezone.utc)
                if window_start_utc <= due_utc <= window_end_utc:
                    for ev in non_all_day_events:
                        ev_start = ev.start_at.astimezone(timezone.utc)
                        ev_end = ev.end_at.astimezone(timezone.utc)
                        if ev_start <= due_utc < ev_end:
                            conflicts.append(AgendaConflict(
                                conflict_id=f"conf_deadline_during_event_{tsk.id}_{ev.event_id}",
                                conflict_type=ConflictType.DEADLINE_DURING_EVENT,
                                severity=ConflictSeverity.WARNING,
                                description=f"Task deadline '{tsk.title}' occurs during calendar event '{ev.title}'.",
                                item_ids=[f"tasks:{tsk.id}", f"calendar:{ev.event_id}"],
                                detected_at=now_utc,
                                suggested_action="Reschedule task deadline or adjust meeting schedule.",
                            ))

        # C. REMINDER_DURING_EVENT: Scheduled reminder firing inside a non-all-day event
        for rem in reminders:
            sched_utc = rem.scheduled_at.astimezone(timezone.utc)
            if window_start_utc <= sched_utc <= window_end_utc:
                for ev in non_all_day_events:
                    ev_start = ev.start_at.astimezone(timezone.utc)
                    ev_end = ev.end_at.astimezone(timezone.utc)
                    if ev_start <= sched_utc < ev_end:
                        conflicts.append(AgendaConflict(
                            conflict_id=f"conf_reminder_during_event_{rem.id}_{ev.event_id}",
                            conflict_type=ConflictType.REMINDER_DURING_EVENT,
                            severity=ConflictSeverity.INFO,
                            description=f"Reminder '{rem.title}' is scheduled to fire during calendar event '{ev.title}'.",
                            item_ids=[f"reminders:{rem.id}", f"calendar:{ev.event_id}"],
                            detected_at=now_utc,
                            suggested_action="Consider snoozing or rescheduling reminder alert.",
                        ))

        # D. TASK_DEADLINE_EXCEEDS_COMMITMENT_TARGET: Task deadline after linked commitment target date
        commitment_map: Dict[str, Commitment] = {c.id: c for c in commitments}
        for tsk in tasks:
            if tsk.source_commitment_id and tsk.due_at:
                cmt = commitment_map.get(tsk.source_commitment_id)
                if cmt and cmt.target_date:
                    tsk_date = tsk.due_at.astimezone(effective_tz).date()
                    cmt_date = cmt.target_date.astimezone(effective_tz).date()
                    if tsk_date > cmt_date:
                        conflicts.append(AgendaConflict(
                            conflict_id=f"conf_deadline_exceeds_target_{tsk.id}_{cmt.id}",
                            conflict_type=ConflictType.TASK_DEADLINE_EXCEEDS_COMMITMENT_TARGET,
                            severity=ConflictSeverity.WARNING,
                            description=f"Task '{tsk.title}' deadline ({tsk_date}) exceeds parent commitment '{cmt.title}' target date ({cmt_date}).",
                            item_ids=[f"tasks:{tsk.id}", f"commitments:{cmt.id}"],
                            detected_at=now_utc,
                            suggested_action="Adjust task deadline or advance commitment target date.",
                        ))

        # E. TASK_OVERDUE: Active task with due_at < now_utc
        for tsk in tasks:
            if tsk.due_at:
                due_utc = tsk.due_at.astimezone(timezone.utc)
                if due_utc < now_utc:
                    conflicts.append(AgendaConflict(
                        conflict_id=f"conf_task_overdue_{tsk.id}",
                        conflict_type=ConflictType.TASK_OVERDUE,
                        severity=ConflictSeverity.WARNING,
                        description=f"Task '{tsk.title}' is overdue.",
                        item_ids=[f"tasks:{tsk.id}"],
                        detected_at=now_utc,
                        suggested_action="Complete or reschedule overdue task.",
                    ))

        # F. COMMITMENT_OVERDUE: Active commitment with target_date < today's local date
        today_local = now_utc.astimezone(effective_tz).date()
        for cmt in commitments:
            if cmt.target_date:
                cmt_date = cmt.target_date.astimezone(effective_tz).date()
                if cmt_date < today_local:
                    conflicts.append(AgendaConflict(
                        conflict_id=f"conf_commitment_overdue_{cmt.id}",
                        conflict_type=ConflictType.COMMITMENT_OVERDUE,
                        severity=ConflictSeverity.INFO,
                        description=f"Commitment '{cmt.title}' target date has passed ({cmt_date}).",
                        item_ids=[f"commitments:{cmt.id}"],
                        detected_at=now_utc,
                        suggested_action="Review commitment status or set new target horizon.",
                    ))

        # Deterministic sort:
        # 1. Severity: WARNING (rank 1) before INFO (rank 0)
        # 2. Earliest conflict ID tie-breaker
        conflicts.sort(key=lambda c: (
            -self._severity_rank(c.severity),
            c.conflict_id
        ))

        return conflicts

    # ── Feasible Slot Proposal ───────────────────────────────────────────────

    async def propose_schedule_slot(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        task_id: str = "",
        duration_minutes: int = 0,
        date_str: Optional[str] = None,
        explicit_tz: Optional[str] = None,
        work_start_hour: int = 9,
        work_end_hour: int = 18,
    ) -> ProposedSlotResponse:
        """
        Evaluates calendar availability and task deadline constraints to propose
        deterministic temporally feasible free slots for completing a task.
        Strictly read-only; does not mutate tasks or create reminders.
        """
        # Validate duration
        if duration_minutes < 15:
            raise ValueError("duration_minutes must be an integer >= 15.")

        # Validate working hours range
        self._validate_working_hours(work_start_hour, work_end_hour)

        # Check permissions: Requires TASKS and CALENDAR
        has_tasks = await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
        if not has_tasks:
            raise PermissionError("Tasks domain is not authorized.")

        has_cal = await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id)
        if not has_cal:
            return ProposedSlotResponse(
                feasible=False,
                task_id=task_id,
                task_title="",
                reason="CALENDAR_UNAUTHORIZED",
                warnings=["Calendar permission is not authorized; cannot determine schedule availability."],
            )

        if user_id not in self.calendar_manager._events:
            return ProposedSlotResponse(
                feasible=False,
                task_id=task_id,
                task_title="",
                reason="NO_SNAPSHOT_REPORTED",
                warnings=["No calendar snapshot reported from client device; cannot verify availability."],
            )

        # Retrieve task
        task = await self.task_manager.get_task(task_id=task_id, user_id=user_id)
        if task is None:
            return ProposedSlotResponse(
                feasible=False,
                task_id=task_id,
                task_title="",
                reason="TASK_NOT_FOUND",
                warnings=[f"Task '{task_id}' not found for user."],
            )

        effective_tz = await self.resolve_effective_timezone(explicit_tz=explicit_tz, user_id=user_id)

        # Determine target evaluation date
        if date_str:
            try:
                target_date = date.fromisoformat(date_str)
            except ValueError:
                raise ValueError(f"Invalid date format '{date_str}'. Expected YYYY-MM-DD.")
        elif task.due_at:
            target_date = task.due_at.astimezone(effective_tz).date()
        else:
            target_date = datetime.now(effective_tz).date()

        # Check linked commitment consistency
        warnings: List[str] = []
        if task.source_commitment_id:
            cmt = await self.commitment_manager.get_commitment(commitment_id=task.source_commitment_id, user_id=user_id)
            if cmt and cmt.target_date:
                cmt_target_date = cmt.target_date.astimezone(effective_tz).date()
                if task.due_at:
                    task_due_date = task.due_at.astimezone(effective_tz).date()
                    if cmt_target_date < task_due_date:
                        warnings.append("LINKED_COMMITMENT_TARGET_PRECEDES_TASK_DEADLINE")
                else:
                    # Task has no due date, expose commitment target as context
                    warnings.append(f"Linked commitment '{cmt.title}' has target date {cmt_target_date}.")

        # Retrieve events for the target day window
        local_day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=effective_tz)
        local_day_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, 999999, tzinfo=effective_tz)
        window_start_utc = local_day_start.astimezone(timezone.utc)
        window_end_utc = local_day_end.astimezone(timezone.utc)

        events, _ = await self.calendar_manager.get_events(
            start_at=window_start_utc,
            end_at=window_end_utc,
            max_results=50,
            user_id=user_id,
        )
        events = events or []

        # Construct candidate working window [W_S, W_E]
        work_start_dt = datetime(target_date.year, target_date.month, target_date.day, work_start_hour, 0, 0, tzinfo=effective_tz).astimezone(timezone.utc)
        work_end_dt = datetime(target_date.year, target_date.month, target_date.day, work_end_hour, 0, 0, tzinfo=effective_tz).astimezone(timezone.utc)

        # Clamping to task deadline: if task has due_at on this day, work window cannot exceed due_at
        deadline_utc = task.due_at.astimezone(timezone.utc) if task.due_at else None
        if deadline_utc:
            # If deadline is before the work day start, no slot can fit
            if deadline_utc <= work_start_dt:
                return ProposedSlotResponse(
                    feasible=False,
                    task_id=task.id,
                    task_title=task.title,
                    deadline_utc=deadline_utc,
                    reason="DEADLINE_BEFORE_WORKING_HOURS",
                    warnings=warnings + ["Task deadline precedes or equals the start of working hours."],
                    max_available_duration_minutes=0,
                )
            work_end_dt = min(work_end_dt, deadline_utc)

        # Compute free gaps in [work_start_dt, work_end_dt]
        candidate_gaps = self._find_gaps_in_window(
            window_start=work_start_dt,
            window_end=work_end_dt,
            events=[e for e in events if not e.all_day],
        )

        # Filter gaps that can fit the requested duration
        feasible_slots: List[FreeSlot] = []
        max_duration = 0
        for gap_start, gap_end in candidate_gaps:
            gap_mins = int((gap_end - gap_start).total_seconds() / 60)
            if gap_mins > max_duration:
                max_duration = gap_mins
            if gap_mins >= duration_minutes:
                # Propose slot starting at gap_start for requested duration
                slot_end = gap_start + timedelta(minutes=duration_minutes)
                feasible_slots.append(FreeSlot(
                    start_at=gap_start,
                    end_at=slot_end,
                    duration_minutes=duration_minutes,
                ))

        # Deterministic tie-breaking:
        # 1. Earliest start_at
        # 2. Largest continuous margin of the underlying gap
        # 3. Lexicographical ISO start timestamp
        def sort_key(slot: FreeSlot) -> Tuple[datetime, int, str]:
            # Find the margin for this slot
            parent_gap_mins = next(
                (int((ge - gs).total_seconds() / 60) for gs, ge in candidate_gaps if gs <= slot.start_at and slot.end_at <= ge),
                duration_minutes
            )
            return (slot.start_at, -parent_gap_mins, slot.start_at.isoformat())

        feasible_slots.sort(key=sort_key)

        is_feasible = len(feasible_slots) > 0
        reason = None if is_feasible else "NO_FEASIBLE_SLOT"

        return ProposedSlotResponse(
            feasible=is_feasible,
            task_id=task.id,
            task_title=task.title,
            deadline_utc=deadline_utc,
            proposed_slots=feasible_slots[:5],  # Top 5 proposals
            warnings=warnings,
            max_available_duration_minutes=max_duration,
            reason=reason,
        )

    # ── Free Slot Helper Math ────────────────────────────────────────────────

    def _calculate_free_slots_for_day(
        self,
        target_date: date,
        events: List[CalendarEvent],
        effective_tz: zoneinfo.ZoneInfo,
        work_start_hour: int,
        work_end_hour: int,
        min_duration_minutes: int = 15,
    ) -> List[FreeSlot]:
        """Calculates free working gaps for an entire date."""
        work_start = datetime(target_date.year, target_date.month, target_date.day, work_start_hour, 0, 0, tzinfo=effective_tz).astimezone(timezone.utc)
        work_end = datetime(target_date.year, target_date.month, target_date.day, work_end_hour, 0, 0, tzinfo=effective_tz).astimezone(timezone.utc)

        gaps = self._find_gaps_in_window(
            window_start=work_start,
            window_end=work_end,
            events=[e for e in events if not e.all_day],
        )

        slots: List[FreeSlot] = []
        for start_dt, end_dt in gaps:
            mins = int((end_dt - start_dt).total_seconds() / 60)
            if mins >= min_duration_minutes:
                slots.append(FreeSlot(
                    start_at=start_dt,
                    end_at=end_dt,
                    duration_minutes=mins,
                ))
        return slots

    def _find_gaps_in_window(
        self,
        window_start: datetime,
        window_end: datetime,
        events: List[CalendarEvent],
    ) -> List[Tuple[datetime, datetime]]:
        """
        Calculates exact complement gaps between non-all-day calendar intervals
        intersecting [window_start, window_end].
        """
        if window_end <= window_start:
            return []

        # 1. Clamp events to [window_start, window_end]
        clamped: List[Tuple[datetime, datetime]] = []
        for ev in events:
            s_utc = ev.start_at.astimezone(timezone.utc)
            e_utc = ev.end_at.astimezone(timezone.utc)
            if s_utc < window_end and e_utc > window_start:
                clamped.append((max(s_utc, window_start), min(e_utc, window_end)))

        # 2. Sort by start ascending
        clamped.sort(key=lambda pair: pair[0])

        # 3. Merge overlapping / contiguous intervals
        merged: List[Tuple[datetime, datetime]] = []
        for start, end in clamped:
            if not merged:
                merged.append((start, end))
            else:
                prev_start, prev_end = merged[-1]
                if start <= prev_end:
                    merged[-1] = (prev_start, max(prev_end, end))
                else:
                    merged.append((start, end))

        # 4. Complement gaps
        gaps: List[Tuple[datetime, datetime]] = []
        current = window_start

        for occ_start, occ_end in merged:
            if current < occ_start:
                gaps.append((current, occ_start))
            current = max(current, occ_end)

        if current < window_end:
            gaps.append((current, window_end))

        return gaps

    # ── Utilities ────────────────────────────────────────────────────────────

    def _validate_working_hours(self, start_hour: int, end_hour: int) -> None:
        if not (0 <= start_hour <= 23) or not (0 <= end_hour <= 23):
            raise ValueError("work_start_hour and work_end_hour must be between 0 and 23.")
        if start_hour >= end_hour:
            raise ValueError("work_start_hour must be strictly less than work_end_hour.")

    def _priority_rank(self, priority: Optional[str]) -> int:
        if priority == "high":
            return 3
        if priority == "medium":
            return 2
        if priority == "low":
            return 1
        return 0

    def _severity_rank(self, severity: ConflictSeverity) -> int:
        if severity == ConflictSeverity.WARNING:
            return 2
        if severity == ConflictSeverity.INFO:
            return 1
        return 0


# Global singleton instance
_default_agenda_service: Optional[AgendaService] = None


def get_agenda_service(
    calendar_manager: Optional[CalendarManager] = None,
    task_manager: Optional[TaskManager] = None,
    commitment_manager: Optional[CommitmentManager] = None,
    reminder_manager: Optional[ReminderManager] = None,
    permission_manager: Optional[PermissionManager] = None,
    identity_manager: Optional[Any] = None,
) -> AgendaService:
    global _default_agenda_service
    if any(m is not None for m in [calendar_manager, task_manager, commitment_manager, reminder_manager, permission_manager, identity_manager]):
        return AgendaService(
            calendar_manager=calendar_manager,
            task_manager=task_manager,
            commitment_manager=commitment_manager,
            reminder_manager=reminder_manager,
            permission_manager=permission_manager,
            identity_manager=identity_manager,
        )
    if _default_agenda_service is None:
        _default_agenda_service = AgendaService()
    return _default_agenda_service


def get_agenda_service_dependency() -> AgendaService:
    """FastAPI dependency provider."""
    return get_agenda_service()
