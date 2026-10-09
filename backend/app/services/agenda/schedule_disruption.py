from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import uuid
import zoneinfo

from app.services.agenda.coordination import resolve_task_duration
from app.services.agenda.schedule_disruption_models import (
    CalendarStateError,
    DiagnoseScheduleDisruptionRequest,
    DiscoveryUniverseSaturatedError,
    InvalidTimezoneError,
    InvalidWindowError,
    Phase6JError,
    ScheduleDisruptionDiagnostic,
    ScheduleDisruptionSeverity,
    TaskDomainUnavailableError,
    TaskJeopardyItem,
    TaskJeopardyReason,
    WorkingHoursUnresolvableError,
)
from app.services.agenda.service import AgendaService
from app.services.calendar.manager import CalendarManager
from app.services.calendar.models import CalendarEvent
from app.services.commitments.manager import CommitmentManager
from app.services.commitments.models import Commitment, CommitmentStatus
from app.services.permissions.manager import PermissionManager
from app.services.permissions.models import PermissionType
from app.services.reminders.manager import ReminderManager
from app.services.reminders.models import Reminder, ReminderStatus
from app.services.tasks.manager import TaskManager
from app.services.tasks.models import Task, TaskStatus

logger = logging.getLogger("aurora.agenda.schedule_disruption")

PAGE_SIZE: int = 50
MAX_DISCOVERY_ENTITIES: int = 500

REASON_RANKS: Dict[TaskJeopardyReason, int] = {
    TaskJeopardyReason.COMMITMENT_OVERRUN_RISK: 1,
    TaskJeopardyReason.OVERDUE: 2,
    TaskJeopardyReason.DEADLINE_DURING_EVENT: 3,
    TaskJeopardyReason.WORKING_HOURS_OVERFLOW: 4,
    TaskJeopardyReason.UNSCHEDULED_WITH_TARGET: 5,
}


class ScheduleDisruptionDiagnosticService:
    """
    Authoritative, read-only diagnostic service for schedule disruption and
    recovery candidate discovery (Phase 6J).
    """

    def __init__(
        self,
        task_manager: TaskManager,
        commitment_manager: CommitmentManager,
        reminder_manager: ReminderManager,
        calendar_manager: CalendarManager,
        agenda_service: AgendaService,
        permission_manager: PermissionManager,
    ):
        self.task_manager = task_manager
        self.commitment_manager = commitment_manager
        self.reminder_manager = reminder_manager
        self.calendar_manager = calendar_manager
        self.agenda_service = agenda_service
        self.permission_manager = permission_manager

    async def _paginate_with_probe(
        self,
        fetch_fn: Callable[..., Any],
        domain_name: str,
    ) -> List[Any]:
        """
        Paginates store entities in chunks of 50 up to 500.
        If exactly 500 items are accumulated, executes a dedicated probe query
        at offset=500, limit=1 to verify universe boundary.
        """
        accumulated: List[Any] = []
        seen_ids: Set[str] = set()
        offset: int = 0

        while offset < MAX_DISCOVERY_ENTITIES:
            page = await fetch_fn(limit=PAGE_SIZE, offset=offset)
            if not page:
                return accumulated

            for item in page:
                if item.id not in seen_ids:
                    seen_ids.add(item.id)
                    accumulated.append(item)

            if len(page) < PAGE_SIZE:
                return accumulated

            offset += PAGE_SIZE

        # Offset reached 500 (10 full pages of 50). Execute dedicated probe query.
        probe_page = await fetch_fn(limit=1, offset=MAX_DISCOVERY_ENTITIES)
        if len(probe_page) == 0:
            return accumulated

        error_code = f"{domain_name.upper()}_UNIVERSE_SATURATED"
        raise DiscoveryUniverseSaturatedError(
            f"Domain '{domain_name}' exceeds maximum allowable ceiling of {MAX_DISCOVERY_ENTITIES} entities. "
            "Diagnostic cannot be computed with incomplete entity universe.",
            error_code=error_code,
        )

    async def diagnose_disruption(
        self,
        authenticated_user_id: str,
        request: DiagnoseScheduleDisruptionRequest,
    ) -> ScheduleDisruptionDiagnostic:
        """
        Authoritative schedule disruption diagnosis and candidate discovery.
        Pure read-only operation.
        """
        if not authenticated_user_id or not authenticated_user_id.strip():
            raise Phase6JError("Authenticated user ID must be provided.", error_code="UNAUTHORIZED")

        now_utc = datetime.now(timezone.utc)

        # 1. Window Normalization & Validation
        target_start = request.target_window_start
        if target_start is None:
            target_start = now_utc
        elif target_start.tzinfo is None:
            target_start = target_start.replace(tzinfo=timezone.utc)
        else:
            target_start = target_start.astimezone(timezone.utc)

        target_end = request.target_window_end
        if target_end is None:
            target_end = target_start + timedelta(hours=48)
        elif target_end.tzinfo is None:
            target_end = target_end.replace(tzinfo=timezone.utc)
        else:
            target_end = target_end.astimezone(timezone.utc)

        if target_start >= target_end:
            raise InvalidWindowError(
                "target_window_start must be strictly less than target_window_end.",
                error_code="INVALID_WINDOW",
            )
        if (target_end - target_start) > timedelta(days=7):
            raise InvalidWindowError(
                "Window duration exceeds maximum limit of 7 days (168 hours).",
                error_code="WINDOW_EXCEEDS_MAX_SPAN",
            )

        # 2. Timezone & Working Hours Validation
        if request.client_preferred_timezone is not None and request.client_preferred_timezone.strip():
            tz_str = request.client_preferred_timezone.strip()
            if tz_str.startswith("+") or tz_str.startswith("-") or any(c in tz_str for c in ["+0", "-0", "+1", "-1"]):
                raise InvalidTimezoneError(
                    f"Invalid IANA timezone identifier: '{tz_str}'. Raw offsets are not permitted.",
                    error_code="INVALID_TIMEZONE",
                )

        try:
            effective_tz = await self.agenda_service.resolve_effective_timezone(
                explicit_tz=request.client_preferred_timezone,
                user_id=authenticated_user_id,
            )
            if not isinstance(effective_tz, zoneinfo.ZoneInfo):
                effective_tz = zoneinfo.ZoneInfo(str(effective_tz))
        except InvalidTimezoneError:
            raise
        except Exception as e:
            raise InvalidTimezoneError(f"Failed to resolve effective timezone: {str(e)}", error_code="INVALID_TIMEZONE")

        try:
            self.agenda_service._validate_working_hours(9, 18)
        except Exception as e:
            raise WorkingHoursUnresolvableError(
                f"Working hours validation failed: {str(e)}",
                error_code="WORKING_HOURS_UNRESOLVABLE",
            )

        # 3. TASKS Permission Gate (Fail Closed)
        is_tasks_auth = await self.permission_manager.is_authorized(
            PermissionType.TASKS, user_id=authenticated_user_id
        )
        if not is_tasks_auth:
            raise TaskDomainUnavailableError(
                "Permission 'tasks' is not authorized. Schedule disruption diagnosis cannot be evaluated "
                "without authoritative access to the primary task workload.",
                error_code="TASK_DOMAIN_UNAVAILABLE",
            )

        omitted_domains: List[str] = []

        # 4. Entity Discovery: Tasks
        tasks_raw = await self._paginate_with_probe(
            fetch_fn=lambda limit, offset: self.task_manager.list_tasks(
                user_id=authenticated_user_id,
                statuses=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS],
                include_inactive=False,
                limit=limit,
                offset=offset,
            ),
            domain_name="task",
        )
        active_tasks = [t for t in tasks_raw if t.is_active and t.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)]

        # 5. Entity Discovery: Commitments
        is_comm_auth = await self.permission_manager.is_authorized(
            PermissionType.COMMITMENTS, user_id=authenticated_user_id
        )
        commitments_by_id: Dict[str, Commitment] = {}
        if is_comm_auth:
            commitments_raw = await self._paginate_with_probe(
                fetch_fn=lambda limit, offset: self.commitment_manager.list_commitments(
                    user_id=authenticated_user_id,
                    statuses=[CommitmentStatus.ACTIVE],
                    include_inactive=False,
                    limit=limit,
                    offset=offset,
                ),
                domain_name="commitment",
            )
            for c in commitments_raw:
                if c.is_active and c.status == CommitmentStatus.ACTIVE:
                    commitments_by_id[c.id] = c
        else:
            omitted_domains.append("commitments")

        # 6. Entity Discovery: Calendar
        is_cal_auth = await self.permission_manager.is_authorized(
            PermissionType.CALENDAR, user_id=authenticated_user_id
        )
        calendar_available: bool = False
        calendar_events: List[CalendarEvent] = []
        if is_cal_auth:
            if not self.calendar_manager.is_snapshot_available(user_id=authenticated_user_id):
                raise CalendarStateError("Calendar snapshot is not available", error_code="NO_CALENDAR_SNAPSHOT")
            if not self.calendar_manager.is_snapshot_fresh(user_id=authenticated_user_id, max_age_seconds=86400, now=now_utc):
                raise CalendarStateError("Calendar snapshot is stale (> 24 hours)", error_code="STALE_CALENDAR_SNAPSHOT")

            try:
                events, _ = await self.calendar_manager.get_events(
                    start_at=target_start,
                    end_at=target_end,
                    max_results=50,
                    user_id=authenticated_user_id,
                )
            except Exception as e:
                raise CalendarStateError(f"Calendar event retrieval failed: {str(e)}", error_code="CALENDAR_RETRIEVAL_FAILED")

            if events is None:
                raise CalendarStateError("Calendar events unavailable", error_code="CALENDAR_RETRIEVAL_FAILED")
            if len(events) == 50:
                raise CalendarStateError("Calendar snapshot saturated (retrieved maximum 50 events)", error_code="CALENDAR_SNAPSHOT_SATURATED")

            calendar_events = events
            calendar_available = True
        else:
            omitted_domains.append("calendar")

        # 7. Entity Discovery: Reminders
        active_reminders: List[Reminder] = []
        if request.include_reminders:
            is_rem_auth = await self.permission_manager.is_authorized(
                PermissionType.REMINDERS, user_id=authenticated_user_id
            )
            if is_rem_auth:
                reminders_raw = await self._paginate_with_probe(
                    fetch_fn=lambda limit, offset: self.reminder_manager.list_reminders(
                        user_id=authenticated_user_id,
                        statuses=[ReminderStatus.SCHEDULED],
                        include_inactive=False,
                        limit=limit,
                        offset=offset,
                    ),
                    domain_name="reminder",
                )
                active_reminders = [r for r in reminders_raw if r.is_active and r.status == ReminderStatus.SCHEDULED]
            else:
                omitted_domains.append("reminders")

        # 8. Partial Evaluation-Window Working Capacity & Calendar Busy-Time
        start_local = target_start.astimezone(effective_tz)
        end_local = target_end.astimezone(effective_tz)

        dates: List[date] = []
        curr_d = start_local.date()
        while curr_d <= end_local.date():
            dates.append(curr_d)
            curr_d += timedelta(days=1)

        total_working_minutes: int = 0
        available_working_minutes: int = 0

        for d in dates:
            local_09 = datetime(d.year, d.month, d.day, 9, 0, 0, tzinfo=effective_tz)
            local_18 = datetime(d.year, d.month, d.day, 18, 0, 0, tzinfo=effective_tz)
            local_work_start_utc = local_09.astimezone(timezone.utc)
            local_work_end_utc = local_18.astimezone(timezone.utc)

            eff_work_start = max(local_work_start_utc, target_start)
            eff_work_end = min(local_work_end_utc, target_end)

            if eff_work_start >= eff_work_end:
                nominal_d = 0
                avail_d = 0
            else:
                nominal_d = int(round((eff_work_end - eff_work_start).total_seconds() / 60.0))
                if calendar_available:
                    clipped_intervals: List[Tuple[datetime, datetime]] = []
                    for ev in calendar_events:
                        if getattr(ev, "all_day", False) or getattr(ev, "is_all_day", False):
                            continue
                        ev_s = ev.start_at.astimezone(timezone.utc) if ev.start_at.tzinfo else ev.start_at.replace(tzinfo=timezone.utc)
                        ev_e = ev.end_at.astimezone(timezone.utc) if ev.end_at.tzinfo else ev.end_at.replace(tzinfo=timezone.utc)
                        s_clip = max(ev_s, eff_work_start)
                        e_clip = min(ev_e, eff_work_end)
                        if s_clip < e_clip:
                            clipped_intervals.append((s_clip, e_clip))

                    merged: List[Tuple[datetime, datetime]] = []
                    for s, e in sorted(clipped_intervals, key=lambda x: (x[0], x[1])):
                        if not merged:
                            merged.append((s, e))
                        else:
                            prev_s, prev_e = merged[-1]
                            if s <= prev_e:
                                merged[-1] = (prev_s, max(prev_e, e))
                            else:
                                merged.append((s, e))
                    busy_d = sum(int(round((e - s).total_seconds() / 60.0)) for s, e in merged)
                    avail_d = max(0, nominal_d - busy_d)
                else:
                    avail_d = nominal_d

            total_working_minutes += nominal_d
            available_working_minutes += avail_d

        # 9. Qualifying Task Workload & Deficit Calculation
        qualifying_tasks: List[Task] = []
        task_durations: Dict[str, int] = {}
        overdue_task_count: int = 0

        for t in active_tasks:
            duration = resolve_task_duration(t, default_duration_minutes=30)
            task_durations[t.id] = duration

            t_due = t.due_at.astimezone(timezone.utc) if t.due_at else None

            if t_due is not None:
                if t_due < now_utc:
                    # Historical overdue
                    qualifying_tasks.append(t)
                    overdue_task_count += 1
                elif now_utc <= t_due <= target_end:
                    # Due within evaluation window
                    qualifying_tasks.append(t)
            else:
                # Unscheduled: check if linked to commitment targeting this window
                linked_id = t.source_commitment_id or (t.metadata.get("commitment_id") if t.metadata else None)
                if linked_id and linked_id in commitments_by_id:
                    c = commitments_by_id[linked_id]
                    if c.target_date:
                        c_target = c.target_date.astimezone(effective_tz).date() if isinstance(c.target_date, datetime) else c.target_date
                        if start_local.date() <= c_target <= end_local.date():
                            qualifying_tasks.append(t)

        required_task_minutes = sum(task_durations[t.id] for t in qualifying_tasks)
        focus_capacity_deficit_minutes = max(0, required_task_minutes - available_working_minutes)

        # 10. Task Jeopardy Classification
        jeopardy_items: List[TaskJeopardyItem] = []

        for t in active_tasks:
            reasons: Set[TaskJeopardyReason] = set()
            t_due = t.due_at.astimezone(timezone.utc) if t.due_at else None
            duration = task_durations[t.id]

            linked_id = t.source_commitment_id or (t.metadata.get("commitment_id") if t.metadata else None)
            linked_comm = commitments_by_id.get(linked_id) if linked_id else None

            comm_target_date_val: Optional[date] = None
            if linked_comm and linked_comm.target_date:
                comm_target_date_val = (
                    linked_comm.target_date.astimezone(effective_tz).date()
                    if isinstance(linked_comm.target_date, datetime)
                    else linked_comm.target_date
                )

            # Reason 1: COMMITMENT_OVERRUN_RISK
            if is_comm_auth and linked_comm and comm_target_date_val and t_due is not None:
                t_due_local_date = t_due.astimezone(effective_tz).date()
                if t_due_local_date > comm_target_date_val:
                    reasons.add(TaskJeopardyReason.COMMITMENT_OVERRUN_RISK)

            # Reason 2: OVERDUE
            if t_due is not None and t_due < now_utc:
                reasons.add(TaskJeopardyReason.OVERDUE)

            # Reason 3: DEADLINE_DURING_EVENT
            if calendar_available and t_due is not None:
                for ev in calendar_events:
                    if getattr(ev, "all_day", False) or getattr(ev, "is_all_day", False):
                        continue
                    ev_s = ev.start_at.astimezone(timezone.utc) if ev.start_at.tzinfo else ev.start_at.replace(tzinfo=timezone.utc)
                    ev_e = ev.end_at.astimezone(timezone.utc) if ev.end_at.tzinfo else ev.end_at.replace(tzinfo=timezone.utc)
                    if ev_s <= t_due <= ev_e:
                        reasons.add(TaskJeopardyReason.DEADLINE_DURING_EVENT)
                        break

            # Reason 4: WORKING_HOURS_OVERFLOW
            if t_due is not None and (t_due < now_utc or t_due <= target_end):
                due_local = t_due.astimezone(effective_tz)
                is_outside_working_hours = (
                    due_local.hour < 9
                    or (due_local.hour == 18 and (due_local.minute > 0 or due_local.second > 0))
                    or due_local.hour > 18
                )
                if is_outside_working_hours or focus_capacity_deficit_minutes > 0:
                    reasons.add(TaskJeopardyReason.WORKING_HOURS_OVERFLOW)

            # Reason 5: UNSCHEDULED_WITH_TARGET
            if t_due is None and is_comm_auth and linked_comm and comm_target_date_val:
                if start_local.date() <= comm_target_date_val <= end_local.date():
                    reasons.add(TaskJeopardyReason.UNSCHEDULED_WITH_TARGET)

            if reasons:
                # Sort reasons strictly by rank
                sorted_reasons = tuple(sorted(reasons, key=lambda r: REASON_RANKS[r]))
                jeopardy_items.append(
                    TaskJeopardyItem(
                        task_id=t.id,
                        title=t.title,
                        current_due_at=t_due,
                        linked_commitment_id=linked_comm.id if linked_comm else None,
                        commitment_target_date=comm_target_date_val,
                        jeopardy_reasons=sorted_reasons,
                        estimated_duration_minutes=duration,
                    )
                )

        # 11. Sort Jeopardy Tasks:
        # (Rank(t), due_at or None last, -duration, task_id)
        max_dt = datetime.max.replace(tzinfo=timezone.utc)

        def sort_key(item: TaskJeopardyItem):
            item_rank = min(REASON_RANKS[r] for r in item.jeopardy_reasons)
            due_sort = item.current_due_at if item.current_due_at is not None else max_dt
            return (item_rank, due_sort, -item.estimated_duration_minutes, item.task_id)

        sorted_jeopardy_tasks = tuple(sorted(jeopardy_items, key=sort_key))

        # 12. Candidate Selection (recommended_recovery_task_ids)
        selected_tasks_set: Set[str] = set()
        candidate_task_ids: List[str] = []

        # Ranks 1, 2, 3: Mandatory candidates
        for item in sorted_jeopardy_tasks:
            item_rank = min(REASON_RANKS[r] for r in item.jeopardy_reasons)
            if item_rank in (1, 2, 3):
                if item.task_id not in selected_tasks_set:
                    selected_tasks_set.add(item.task_id)
                    candidate_task_ids.append(item.task_id)

        # Ranks 4, 5: Deficit-clearing candidates (only if deficit > 0)
        if focus_capacity_deficit_minutes > 0:
            mandatory_minutes = sum(
                item.estimated_duration_minutes
                for item in sorted_jeopardy_tasks
                if item.task_id in selected_tasks_set
            )
            remaining_deficit = max(0, focus_capacity_deficit_minutes - mandatory_minutes)
            if remaining_deficit > 0:
                cleared_minutes = 0
                for item in sorted_jeopardy_tasks:
                    item_rank = min(REASON_RANKS[r] for r in item.jeopardy_reasons)
                    if item_rank in (4, 5) and item.task_id not in selected_tasks_set:
                        selected_tasks_set.add(item.task_id)
                        candidate_task_ids.append(item.task_id)
                        cleared_minutes += item.estimated_duration_minutes
                        if cleared_minutes >= remaining_deficit:
                            break

        recommended_recovery_task_ids = tuple(candidate_task_ids)

        # 13. Candidate Reminders (recommended_recovery_reminder_ids)
        candidate_reminders: List[Reminder] = []
        if request.include_reminders and "reminders" not in omitted_domains:
            for r in active_reminders:
                r_sched = r.scheduled_at.astimezone(timezone.utc) if r.scheduled_at.tzinfo else r.scheduled_at.replace(tzinfo=timezone.utc)
                is_candidate = False
                if r.task_id and r.task_id in selected_tasks_set:
                    is_candidate = True
                elif calendar_available:
                    for ev in calendar_events:
                        if getattr(ev, "all_day", False) or getattr(ev, "is_all_day", False):
                            continue
                        ev_s = ev.start_at.astimezone(timezone.utc) if ev.start_at.tzinfo else ev.start_at.replace(tzinfo=timezone.utc)
                        ev_e = ev.end_at.astimezone(timezone.utc) if ev.end_at.tzinfo else ev.end_at.replace(tzinfo=timezone.utc)
                        if ev_s <= r_sched <= ev_e:
                            is_candidate = True
                            break
                if is_candidate:
                    candidate_reminders.append(r)

            candidate_reminders.sort(key=lambda r: (r.scheduled_at, r.id))

        recommended_recovery_reminder_ids = tuple(r.id for r in candidate_reminders)

        # 14. Deterministic Severity Algorithm
        if (
            focus_capacity_deficit_minutes >= 120
            or overdue_task_count >= 3
            or any(min(REASON_RANKS[r] for r in item.jeopardy_reasons) == 1 for item in sorted_jeopardy_tasks)
        ):
            severity = ScheduleDisruptionSeverity.CRITICAL
        elif (
            focus_capacity_deficit_minutes > 0
            or overdue_task_count >= 1
            or any(min(REASON_RANKS[r] for r in item.jeopardy_reasons) == 3 for item in sorted_jeopardy_tasks)
        ):
            severity = ScheduleDisruptionSeverity.MODERATE
        elif (
            (focus_capacity_deficit_minutes == 0 and overdue_task_count == 0 and any(min(REASON_RANKS[r] for r in item.jeopardy_reasons) in (4, 5) for item in sorted_jeopardy_tasks))
            or ("commitments" in omitted_domains and any(getattr(t, "source_commitment_id", None) or (t.metadata and t.metadata.get("commitment_id")) for t in active_tasks))
            or ("calendar" in omitted_domains)
        ):
            severity = ScheduleDisruptionSeverity.MINOR
        else:
            severity = ScheduleDisruptionSeverity.NONE

        diagnostic_id = f"diag_{uuid.uuid4().hex[:16]}"

        return ScheduleDisruptionDiagnostic(
            diagnostic_id=diagnostic_id,
            computed_at=now_utc,
            evaluation_window_start=target_start,
            evaluation_window_end=target_end,
            timezone=effective_tz.key if hasattr(effective_tz, "key") else str(effective_tz),
            severity=severity,
            total_working_minutes=total_working_minutes,
            available_working_minutes=available_working_minutes,
            required_task_minutes=required_task_minutes,
            focus_capacity_deficit_minutes=focus_capacity_deficit_minutes,
            overdue_task_count=overdue_task_count,
            jeopardy_tasks=sorted_jeopardy_tasks,
            recommended_recovery_task_ids=recommended_recovery_task_ids,
            recommended_recovery_reminder_ids=recommended_recovery_reminder_ids,
            omitted_domains=tuple(sorted(omitted_domains)),
        )


_diagnostic_service_instance: Optional[ScheduleDisruptionDiagnosticService] = None


def get_schedule_disruption_diagnostic_service() -> ScheduleDisruptionDiagnosticService:
    global _diagnostic_service_instance
    if _diagnostic_service_instance is None:
        from app.services.tasks.manager import get_task_manager
        from app.services.reminders.manager import get_reminder_manager
        from app.services.commitments.manager import get_commitment_manager
        from app.services.calendar.manager import get_calendar_manager
        from app.services.agenda.service import get_agenda_service
        from app.services.permissions.manager import get_permission_manager

        _diagnostic_service_instance = ScheduleDisruptionDiagnosticService(
            task_manager=get_task_manager(),
            commitment_manager=get_commitment_manager(),
            reminder_manager=get_reminder_manager(),
            calendar_manager=get_calendar_manager(),
            agenda_service=get_agenda_service(),
            permission_manager=get_permission_manager(),
        )
    return _diagnostic_service_instance


def get_diagnostic_service_dependency() -> ScheduleDisruptionDiagnosticService:
    return get_schedule_disruption_diagnostic_service()
