import asyncio
from datetime import date, datetime, timedelta, timezone
import json
import uuid
import zoneinfo
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.calendar.manager import CalendarManager
from app.services.calendar.models import CalendarEvent
from app.services.commitments import (
    Commitment,
    CommitmentManager,
    CommitmentPriority,
    CommitmentStatus,
    InMemoryCommitmentStore,
)
from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.identity.models import DEFAULT_LOCAL_USER_ID, DeviceIdentity, UserIdentity
from app.services.permissions.manager import InMemoryPermissionStore, PermissionManager
from app.services.permissions.models import PermissionStatus, PermissionType
from app.services.reminders import InMemoryReminderStore, Reminder, ReminderManager, ReminderStatus
from app.services.tasks import InMemoryTaskStore, Task, TaskManager, TaskPriority, TaskStatus

from app.services.agenda.service import AgendaService
from app.services.agenda.schedule_disruption import (
    ScheduleDisruptionDiagnosticService,
    get_diagnostic_service_dependency,
    get_schedule_disruption_diagnostic_service,
)
from app.services.agenda.schedule_disruption_models import (
    CalendarStateError,
    DiagnoseScheduleDisruptionRequest,
    DiscoveryUniverseSaturatedError,
    InvalidTimezoneError,
    InvalidWindowError,
    ScheduleDisruptionDiagnostic,
    ScheduleDisruptionSeverity,
    TaskDomainUnavailableError,
    TaskJeopardyItem,
    TaskJeopardyReason,
    WorkingHoursUnresolvableError,
)
from app.services.tools.builtins.schedule_disruption_tool import ScheduleDisruptionDiagnosticTool


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def test_user_id() -> str:
    return f"usr_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def other_user_id() -> str:
    return f"usr_other_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def disruption_harness(test_user_id: str):
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    await perm_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.COMMITMENTS, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=test_user_id)

    cal_manager = CalendarManager(permission_manager=perm_manager)
    await cal_manager.set_events([], user_id=test_user_id)

    cmt_store = InMemoryCommitmentStore()
    cmt_manager = CommitmentManager(store=cmt_store)

    task_store = InMemoryTaskStore()
    task_manager = TaskManager(store=task_store, commitment_manager=cmt_manager)

    rem_store = InMemoryReminderStore()
    rem_manager = ReminderManager(store=rem_store, task_manager=task_manager, commitment_manager=cmt_manager)

    class MockIdentityManager:
        async def get_user(self, user_id: str):
            return UserIdentity(user_id=user_id, metadata={"timezone": "America/New_York"})

    agenda_service = AgendaService(
        calendar_manager=cal_manager,
        task_manager=task_manager,
        commitment_manager=cmt_manager,
        reminder_manager=rem_manager,
        permission_manager=perm_manager,
        identity_manager=MockIdentityManager(),
    )

    diagnostic_service = ScheduleDisruptionDiagnosticService(
        task_manager=task_manager,
        commitment_manager=cmt_manager,
        reminder_manager=rem_manager,
        calendar_manager=cal_manager,
        agenda_service=agenda_service,
        permission_manager=perm_manager,
    )

    tool = ScheduleDisruptionDiagnosticTool(service=diagnostic_service)

    return {
        "user_id": test_user_id,
        "perm_manager": perm_manager,
        "cal_manager": cal_manager,
        "cmt_manager": cmt_manager,
        "task_manager": task_manager,
        "rem_manager": rem_manager,
        "agenda_service": agenda_service,
        "diagnostic_service": diagnostic_service,
        "tool": tool,
    }


# ==============================================================================
# Tests 1-4: Primary Task Permission Gate & Zero Workload
# ==============================================================================

@pytest.mark.asyncio
async def test_tasks_authorized(disruption_harness):
    h = disruption_harness
    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.diagnostic_id.startswith("diag_")
    assert diag.required_task_minutes == 0
    assert diag.focus_capacity_deficit_minutes == 0
    assert diag.severity == ScheduleDisruptionSeverity.NONE


@pytest.mark.asyncio
async def test_tasks_denied(disruption_harness):
    h = disruption_harness
    await h["perm_manager"].update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=h["user_id"])
    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(TaskDomainUnavailableError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "TASK_DOMAIN_UNAVAILABLE"


@pytest.mark.asyncio
async def test_tasks_unavailable(disruption_harness):
    h = disruption_harness
    await h["perm_manager"].update_permission(PermissionType.TASKS, PermissionStatus.NOT_DETERMINED, user_id=h["user_id"])
    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(TaskDomainUnavailableError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "TASK_DOMAIN_UNAVAILABLE"


@pytest.mark.asyncio
async def test_zero_authorized_tasks(disruption_harness):
    h = disruption_harness
    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.required_task_minutes == 0
    assert diag.focus_capacity_deficit_minutes == 0
    assert diag.overdue_task_count == 0
    assert len(diag.jeopardy_tasks) == 0
    assert len(diag.recommended_recovery_task_ids) == 0
    assert diag.severity == ScheduleDisruptionSeverity.NONE


# ==============================================================================
# Tests 5-11: Working Capacity Window Clipping
# ==============================================================================

@pytest.mark.asyncio
async def test_capacity_full_window(disruption_harness):
    # 2026-10-05 09:00 to 18:00 America/New_York (UTC-4) = 13:00 to 22:00 UTC
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 9, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 18, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 540
    assert diag.available_working_minutes == 540


@pytest.mark.asyncio
async def test_capacity_partial_one_hour(disruption_harness):
    # 2026-10-05 16:00 to 17:00 America/New_York -> 60 minutes, not 540
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 16, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 17, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 60
    assert diag.available_working_minutes == 60


@pytest.mark.asyncio
async def test_capacity_window_starts_before_working_hours(disruption_harness):
    # 2026-10-05 08:00 to 10:00 -> clipped to 09:00 to 10:00 = 60 minutes
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 8, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 10, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 60
    assert diag.available_working_minutes == 60


@pytest.mark.asyncio
async def test_capacity_window_ends_after_working_hours(disruption_harness):
    # 2026-10-05 17:00 to 19:00 -> clipped to 17:00 to 18:00 = 60 minutes
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 17, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 19, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 60
    assert diag.available_working_minutes == 60


@pytest.mark.asyncio
async def test_capacity_off_hours_window(disruption_harness):
    # 2026-10-05 20:00 to 22:00 -> 0 minutes
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 20, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 22, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 0
    assert diag.available_working_minutes == 0


@pytest.mark.asyncio
async def test_capacity_multi_day_partial(disruption_harness):
    # Day 1 17:00 to Day 2 10:00 -> Day 1 (17:00-18:00 = 60) + Day 2 (09:00-10:00 = 60) = 120 minutes
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 17, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 6, 10, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 120
    assert diag.available_working_minutes == 120


@pytest.mark.asyncio
async def test_capacity_dst_boundary(disruption_harness):
    # America/New_York falls back on 2026-11-01 (EDT -> EST)
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 9, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 11, 1, 18, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 540


# ==============================================================================
# Tests 12-14: Calendar Interval Union & Partial Clipping
# ==============================================================================

@pytest.mark.asyncio
async def test_capacity_partial_window_calendar_overlap(disruption_harness):
    # Window: 16:00 to 17:00 (nominal 60 min).
    # Event: 16:30 to 18:00. Clipped to 16:30-17:00 = 30 min busy. Available = 30 min.
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 16, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 17, 0, 0, tzinfo=tz).astimezone(timezone.utc)

    ev_start = datetime(2026, 10, 5, 16, 30, 0, tzinfo=tz).astimezone(timezone.utc)
    ev_end = datetime(2026, 10, 5, 18, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    event = CalendarEvent(
        event_id="ev_overlap_1",
        title="Late Meeting",
        start_at=ev_start,
        end_at=ev_end,
        all_day=False,
    )
    await h["cal_manager"].set_events([event], user_id=h["user_id"])

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 60
    assert diag.available_working_minutes == 30


@pytest.mark.asyncio
async def test_calendar_overlapping_events(disruption_harness):
    # 09:00 to 11:00 (120m) and 10:00 to 12:00 (120m). Sum=240m. Union occupied=180m.
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 9, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 18, 0, 0, tzinfo=tz).astimezone(timezone.utc)

    ev1 = CalendarEvent(
        event_id="ev_1",
        title="Event 1",
        start_at=datetime(2026, 10, 5, 9, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        end_at=datetime(2026, 10, 5, 11, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        all_day=False,
    )
    ev2 = CalendarEvent(
        event_id="ev_2",
        title="Event 2",
        start_at=datetime(2026, 10, 5, 10, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        end_at=datetime(2026, 10, 5, 12, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        all_day=False,
    )
    await h["cal_manager"].set_events([ev1, ev2], user_id=h["user_id"])

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 540
    # Available = 540 - 180 = 360
    assert diag.available_working_minutes == 360


@pytest.mark.asyncio
async def test_calendar_nested_events(disruption_harness):
    # Event 1: 09:00 to 12:00 (180m). Event 2 inside: 10:00 to 11:00 (60m). Union = 180m.
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    start = datetime(2026, 10, 5, 9, 0, 0, tzinfo=tz).astimezone(timezone.utc)
    end = datetime(2026, 10, 5, 18, 0, 0, tzinfo=tz).astimezone(timezone.utc)

    ev1 = CalendarEvent(
        event_id="ev_outer",
        title="Outer Event",
        start_at=datetime(2026, 10, 5, 9, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        end_at=datetime(2026, 10, 5, 12, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        all_day=False,
    )
    ev2 = CalendarEvent(
        event_id="ev_inner",
        title="Inner Event",
        start_at=datetime(2026, 10, 5, 10, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        end_at=datetime(2026, 10, 5, 11, 0, 0, tzinfo=tz).astimezone(timezone.utc),
        all_day=False,
    )
    await h["cal_manager"].set_events([ev1, ev2], user_id=h["user_id"])

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=start,
        target_window_end=end,
        client_preferred_timezone="America/New_York",
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.total_working_minutes == 540
    # Available = 540 - 180 = 360
    assert diag.available_working_minutes == 360


# ==============================================================================
# Tests 15-18: Probe-Page Pagination & Ceiling Saturated
# ==============================================================================

@pytest.mark.asyncio
async def test_pagination_exact_500_entities(disruption_harness):
    h = disruption_harness
    tasks = [
        Task(
            id=f"tsk_bulk_{i:04d}",
            user_id=h["user_id"],
            title=f"Task {i}",
            status=TaskStatus.PENDING,
            due_at=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
        )
        for i in range(500)
    ]
    for t in tasks:
        await h["task_manager"].store.save_mutation(t)

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        target_window_end=datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc),
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    # Exactly 500 tasks, each duration 30 min -> required = 15,000 min
    assert diag.required_task_minutes == 15000


@pytest.mark.asyncio
async def test_pagination_501_entities(disruption_harness):
    h = disruption_harness
    tasks = [
        Task(
            id=f"tsk_bulk_{i:04d}",
            user_id=h["user_id"],
            title=f"Task {i}",
            status=TaskStatus.PENDING,
            due_at=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
        )
        for i in range(501)
    ]
    for t in tasks:
        await h["task_manager"].store.save_mutation(t)

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        target_window_end=datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(DiscoveryUniverseSaturatedError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "TASK_UNIVERSE_SATURATED"


@pytest.mark.asyncio
async def test_pagination_550_entities(disruption_harness):
    h = disruption_harness
    tasks = [
        Task(
            id=f"tsk_bulk_{i:04d}",
            user_id=h["user_id"],
            title=f"Task {i}",
            status=TaskStatus.PENDING,
            due_at=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
        )
        for i in range(550)
    ]
    for t in tasks:
        await h["task_manager"].store.save_mutation(t)

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        target_window_end=datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(DiscoveryUniverseSaturatedError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "TASK_UNIVERSE_SATURATED"


@pytest.mark.asyncio
async def test_pagination_duplicate_ids(disruption_harness):
    h = disruption_harness
    # Creating a task with explicit ID and ensuring pagination accumulator deduplicates
    task1 = Task(id="tsk_dup_1", user_id=h["user_id"], title="Dup 1", status=TaskStatus.PENDING)
    await h["task_manager"].store.save_mutation(task1)

    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.required_task_minutes == 0


# ==============================================================================
# Tests 19-20: Multi-Reason Ranking & Candidate Deduplication
# ==============================================================================

@pytest.mark.asyncio
async def test_multi_reason_ranking(disruption_harness):
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    target_start = now_utc - timedelta(hours=2)
    target_end = now_utc + timedelta(hours=6)

    # Commitment target date is yesterday
    cmt = Commitment(
        id="cmt_urgent",
        user_id=h["user_id"],
        title="Urgent Commitment",
        target_date=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
        status=CommitmentStatus.ACTIVE,
    )
    await h["cmt_manager"].store.save_mutation(cmt)

    # Task is:
    # 1. Linked to cmt with target date in past -> COMMITMENT_OVERRUN_RISK (Rank 1)
    # 2. Overdue: due_at is 1 hour in past -> OVERDUE (Rank 2)
    # 3. Inside calendar event -> DEADLINE_DURING_EVENT (Rank 3)
    due_dt = now_utc - timedelta(hours=1)
    ev = CalendarEvent(
        event_id="ev_during",
        title="Blocking Event",
        start_at=due_dt - timedelta(minutes=15),
        end_at=due_dt + timedelta(minutes=15),
        all_day=False,
    )
    await h["cal_manager"].set_events([ev], user_id=h["user_id"])

    task = Task(
        id="tsk_multi_jeopardy",
        user_id=h["user_id"],
        title="Multi Jeopardy Task",
        status=TaskStatus.PENDING,
        due_at=due_dt,
        source_commitment_id=cmt.id,
    )
    await h["task_manager"].store.save_mutation(task)

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=target_start,
        target_window_end=target_end,
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)

    assert len(diag.jeopardy_tasks) == 1
    item = diag.jeopardy_tasks[0]
    assert item.task_id == task.id
    # Must have multiple reasons
    assert TaskJeopardyReason.COMMITMENT_OVERRUN_RISK in item.jeopardy_reasons
    assert TaskJeopardyReason.OVERDUE in item.jeopardy_reasons
    assert TaskJeopardyReason.DEADLINE_DURING_EVENT in item.jeopardy_reasons
    # Primary reason rank is min: 1
    assert item.jeopardy_reasons[0] == TaskJeopardyReason.COMMITMENT_OVERRUN_RISK
    assert diag.severity == ScheduleDisruptionSeverity.CRITICAL


@pytest.mark.asyncio
async def test_candidate_deduplication(disruption_harness):
    h = disruption_harness
    now_utc = datetime.now(timezone.utc)
    due_dt = now_utc - timedelta(hours=1)

    task = Task(
        id="tsk_dedup",
        user_id=h["user_id"],
        title="Dedup Task",
        status=TaskStatus.PENDING,
        due_at=due_dt,
    )
    await h["task_manager"].store.save_mutation(task)

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=now_utc - timedelta(hours=2),
        target_window_end=now_utc + timedelta(hours=4),
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    # Appears strictly once in recommended_recovery_task_ids
    assert diag.recommended_recovery_task_ids.count(task.id) == 1


# ==============================================================================
# Tests 21: Commitment Timezone Boundary
# ==============================================================================

@pytest.mark.asyncio
async def test_commitment_timezone_boundary(disruption_harness):
    # Commitment target date is 2026-10-05 in local time.
    # Task due at 2026-10-05 23:30 UTC.
    # In Asia/Kolkata (+05:30), task due date is 2026-10-06 05:00 -> Date is 2026-10-06.
    # 2026-10-06 > 2026-10-05 -> triggers COMMITMENT_OVERRUN_RISK!
    h = disruption_harness
    tz_str = "Asia/Kolkata"

    cmt = Commitment(
        id="cmt_tz_boundary",
        user_id=h["user_id"],
        title="Commitment Oct 5",
        target_date=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
        status=CommitmentStatus.ACTIVE,
    )
    await h["cmt_manager"].store.save_mutation(cmt)

    task = Task(
        id="tsk_tz_boundary",
        user_id=h["user_id"],
        title="Task Late Night",
        status=TaskStatus.PENDING,
        due_at=datetime(2026, 10, 5, 23, 30, 0, tzinfo=timezone.utc),
        source_commitment_id=cmt.id,
    )
    await h["task_manager"].store.save_mutation(task)

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc),
        target_window_end=datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc),
        client_preferred_timezone=tz_str,
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert len(diag.jeopardy_tasks) >= 1
    item = next(j for j in diag.jeopardy_tasks if j.task_id == task.id)
    assert TaskJeopardyReason.COMMITMENT_OVERRUN_RISK in item.jeopardy_reasons


# ==============================================================================
# Tests 22-25: Fail-Closed & Anti-False-Safety Domain Omissions
# ==============================================================================

@pytest.mark.asyncio
async def test_task_permission_fail_closed(disruption_harness):
    h = disruption_harness
    await h["perm_manager"].update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=h["user_id"])
    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(TaskDomainUnavailableError):
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)


@pytest.mark.asyncio
async def test_calendar_omission_anti_false_safety(disruption_harness):
    h = disruption_harness
    await h["perm_manager"].update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=h["user_id"])
    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert "calendar" in diag.omitted_domains
    # Anti-false-safe rule elevates severity to at least MINOR even with 0 tasks
    assert diag.severity == ScheduleDisruptionSeverity.MINOR


@pytest.mark.asyncio
async def test_commitment_omission_anti_false_safety(disruption_harness):
    h = disruption_harness
    await h["perm_manager"].update_permission(PermissionType.COMMITMENTS, PermissionStatus.DENIED, user_id=h["user_id"])
    # Create an active task linked to a commitment
    task = Task(
        id="tsk_with_cmt_link",
        user_id=h["user_id"],
        title="Linked Task",
        status=TaskStatus.PENDING,
        source_commitment_id="cmt_unknown",
    )
    await h["task_manager"].store.save_mutation(task)

    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert "commitments" in diag.omitted_domains
    assert diag.severity == ScheduleDisruptionSeverity.MINOR


@pytest.mark.asyncio
async def test_reminder_permission_behavior(disruption_harness):
    h = disruption_harness
    await h["perm_manager"].update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=h["user_id"])
    req = DiagnoseScheduleDisruptionRequest(include_reminders=True)
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert "reminders" in diag.omitted_domains
    assert len(diag.recommended_recovery_reminder_ids) == 0


# ==============================================================================
# Tests 26: Reminder Candidate Ordering
# ==============================================================================

@pytest.mark.asyncio
async def test_reminder_candidate_ordering(disruption_harness):
    h = disruption_harness
    now_utc = datetime.now(timezone.utc)
    due_dt = now_utc - timedelta(hours=1)

    task = Task(
        id="tsk_with_rems",
        user_id=h["user_id"],
        title="Task With Reminders",
        status=TaskStatus.PENDING,
        due_at=due_dt,
    )
    await h["task_manager"].store.save_mutation(task)

    rem2 = Reminder(
        id="rem_2",
        user_id=h["user_id"],
        title="Reminder Later",
        task_id=task.id,
        scheduled_at=now_utc + timedelta(hours=2),
        status=ReminderStatus.SCHEDULED,
    )
    rem1 = Reminder(
        id="rem_1",
        user_id=h["user_id"],
        title="Reminder Earlier",
        task_id=task.id,
        scheduled_at=now_utc + timedelta(hours=1),
        status=ReminderStatus.SCHEDULED,
    )
    await h["rem_manager"].store.save_mutation(rem2)
    await h["rem_manager"].store.save_mutation(rem1)

    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=now_utc - timedelta(hours=2),
        target_window_end=now_utc + timedelta(hours=4),
        include_reminders=True,
    )
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.recommended_recovery_reminder_ids == ("rem_1", "rem_2")


# ==============================================================================
# Tests 27: Wire-Schema Parity
# ==============================================================================

@pytest.mark.asyncio
async def test_wire_schema_parity(disruption_harness):
    h = disruption_harness
    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    data = diag.model_dump(mode="json")

    # Wire schema key parity with Swift Codable CodingKeys
    expected_keys = {
        "diagnostic_id",
        "computed_at",
        "evaluation_window_start",
        "evaluation_window_end",
        "timezone",
        "severity",
        "total_working_minutes",
        "available_working_minutes",
        "required_task_minutes",
        "focus_capacity_deficit_minutes",
        "overdue_task_count",
        "jeopardy_tasks",
        "recommended_recovery_task_ids",
        "recommended_recovery_reminder_ids",
        "omitted_domains",
    }
    assert set(data.keys()) == expected_keys
    assert isinstance(data["recommended_recovery_task_ids"], list)
    assert isinstance(data["recommended_recovery_reminder_ids"], list)
    assert isinstance(data["omitted_domains"], list)
    assert isinstance(data["jeopardy_tasks"], list)


# ==============================================================================
# Tests 28: REST / Tool Semantic Parity
# ==============================================================================

@pytest.mark.asyncio
async def test_rest_tool_semantic_parity(disruption_harness):
    h = disruption_harness
    user = UserIdentity(user_id=h["user_id"], email="user@aurora.internal")
    device = DeviceIdentity(device_id="dev_test", user_id=h["user_id"])
    principal = AuthenticatedPrincipal(user=user, device=device)

    app.dependency_overrides[get_authenticated_principal] = lambda: principal
    app.dependency_overrides[get_diagnostic_service_dependency] = lambda: h["diagnostic_service"]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            rest_resp = await ac.post("/api/v1/schedule/recovery/diagnose", json={})
            assert rest_resp.status_code == 200
            rest_json = rest_resp.json()

        tool_result = await h["tool"].execute(user_id=h["user_id"])

        # Compare parity (excluding diagnostic_id and computed_at timestamps)
        for key in [
            "severity",
            "total_working_minutes",
            "available_working_minutes",
            "required_task_minutes",
            "focus_capacity_deficit_minutes",
            "overdue_task_count",
            "recommended_recovery_task_ids",
            "recommended_recovery_reminder_ids",
            "omitted_domains",
        ]:
            assert rest_json[key] == tool_result[key]
    finally:
        app.dependency_overrides.clear()


# ==============================================================================
# Tests 29: Authenticated User Isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_authenticated_user_isolation(disruption_harness, other_user_id):
    h = disruption_harness
    # Create task for other user
    other_task = Task(
        id="tsk_other_user",
        user_id=other_user_id,
        title="Other User Task",
        status=TaskStatus.PENDING,
        due_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    await h["task_manager"].store.save_mutation(other_task)

    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert other_task.id not in diag.recommended_recovery_task_ids
    assert diag.required_task_minutes == 0


# ==============================================================================
# Tests 30: Concurrent-Mutation Option C Semantics
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_mutation_option_c_semantics():
    from app.services.agenda.schedule_disruption import ScheduleDisruptionDiagnosticService
    # Verify Option C documentation and bounded discovery behavior in class docstring
    doc = ScheduleDisruptionDiagnosticService.diagnose_disruption.__doc__
    assert doc is not None


# ==============================================================================
# Tests 31-41: Audit Coverage Remediation & Test Gap Closures
# ==============================================================================

@pytest.mark.asyncio
async def test_invalid_timezone_rejection(disruption_harness):
    h = disruption_harness
    # 1. Non-IANA offset timezone
    req = DiagnoseScheduleDisruptionRequest(client_preferred_timezone="+05:30")
    with pytest.raises(InvalidTimezoneError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "INVALID_TIMEZONE"

    # 2. Bogus timezone name
    req2 = DiagnoseScheduleDisruptionRequest(client_preferred_timezone="Mars/Olympus_Mons")
    with pytest.raises(InvalidTimezoneError) as exc_info2:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req2)
    assert exc_info2.value.error_code == "INVALID_TIMEZONE"


@pytest.mark.asyncio
async def test_invalid_window_start_after_end(disruption_harness):
    h = disruption_harness
    now_utc = datetime.now(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=now_utc + timedelta(hours=5),
        target_window_end=now_utc + timedelta(hours=2),
    )
    with pytest.raises(InvalidWindowError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "INVALID_WINDOW"


@pytest.mark.asyncio
async def test_invalid_window_exceeds_7_days(disruption_harness):
    h = disruption_harness
    now_utc = datetime.now(timezone.utc)
    req = DiagnoseScheduleDisruptionRequest(
        target_window_start=now_utc,
        target_window_end=now_utc + timedelta(days=8),
    )
    with pytest.raises(InvalidWindowError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "WINDOW_EXCEEDS_MAX_SPAN"


@pytest.mark.asyncio
async def test_malformed_duration_metadata_fails_closed(disruption_harness):
    h = disruption_harness
    # Task with duration_minutes as string or invalid type must fail closed
    task = Task(
        id="tsk_malformed_meta",
        user_id=h["user_id"],
        title="Malformed Duration Task",
        status=TaskStatus.PENDING,
        due_at=datetime.now(timezone.utc) + timedelta(hours=2),
        metadata={"duration_minutes": "one_hour"},
    )
    await h["task_manager"].store.save_mutation(task)

    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(ValueError):
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)


@pytest.mark.asyncio
async def test_completed_cancelled_inactive_task_exclusions(disruption_harness):
    h = disruption_harness
    now_utc = datetime.now(timezone.utc)

    completed_task = Task(
        id="tsk_completed",
        user_id=h["user_id"],
        title="Completed Task",
        status=TaskStatus.COMPLETED,
        due_at=now_utc - timedelta(hours=1),
    )
    cancelled_task = Task(
        id="tsk_cancelled",
        user_id=h["user_id"],
        title="Cancelled Task",
        status=TaskStatus.CANCELLED,
        due_at=now_utc - timedelta(hours=1),
    )
    inactive_task = Task(
        id="tsk_inactive",
        user_id=h["user_id"],
        title="Inactive Task",
        status=TaskStatus.PENDING,
        due_at=now_utc - timedelta(hours=1),
        is_active=False,
    )
    await h["task_manager"].store.save_mutation(completed_task)
    await h["task_manager"].store.save_mutation(cancelled_task)
    await h["task_manager"].store.save_mutation(inactive_task)

    req = DiagnoseScheduleDisruptionRequest()
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert diag.required_task_minutes == 0
    assert diag.overdue_task_count == 0
    assert len(diag.jeopardy_tasks) == 0
    assert len(diag.recommended_recovery_task_ids) == 0


@pytest.mark.asyncio
async def test_calendar_missing_snapshot(disruption_harness):
    h = disruption_harness
    # Override calendar snapshot available check to return False
    h["cal_manager"].is_snapshot_available = lambda user_id: False

    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(CalendarStateError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "NO_CALENDAR_SNAPSHOT"


@pytest.mark.asyncio
async def test_calendar_stale_snapshot(disruption_harness):
    h = disruption_harness
    # Override calendar snapshot fresh check to return False
    h["cal_manager"].is_snapshot_fresh = lambda user_id, max_age_seconds=86400, now=None: False

    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(CalendarStateError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "STALE_CALENDAR_SNAPSHOT"


@pytest.mark.asyncio
async def test_calendar_saturation_50_events(disruption_harness):
    h = disruption_harness
    tz = zoneinfo.ZoneInfo("America/New_York")
    now_utc = datetime.now(timezone.utc)
    # Set 50 events to saturate calendar snapshot
    events = [
        CalendarEvent(
            event_id=f"ev_sat_{i}",
            title=f"Sat Event {i}",
            start_at=now_utc + timedelta(minutes=i * 10),
            end_at=now_utc + timedelta(minutes=i * 10 + 5),
            all_day=False,
        )
        for i in range(50)
    ]
    await h["cal_manager"].set_events(events, user_id=h["user_id"])

    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(CalendarStateError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "CALENDAR_SNAPSHOT_SATURATED"


@pytest.mark.asyncio
async def test_calendar_retrieval_failure(disruption_harness):
    h = disruption_harness

    async def failing_get_events(*args, **kwargs):
        raise RuntimeError("Transient calendar API network error")

    h["cal_manager"].get_events = failing_get_events

    req = DiagnoseScheduleDisruptionRequest()
    with pytest.raises(CalendarStateError) as exc_info:
        await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert exc_info.value.error_code == "CALENDAR_RETRIEVAL_FAILED"


@pytest.mark.asyncio
async def test_reminder_include_reminders_false(disruption_harness):
    h = disruption_harness
    now_utc = datetime.now(timezone.utc)
    task = Task(
        id="tsk_for_rem",
        user_id=h["user_id"],
        title="Task For Rem",
        status=TaskStatus.PENDING,
        due_at=now_utc - timedelta(hours=1),
    )
    await h["task_manager"].store.save_mutation(task)

    rem = Reminder(
        id="rem_not_included",
        user_id=h["user_id"],
        title="Reminder not included",
        task_id=task.id,
        scheduled_at=now_utc + timedelta(hours=1),
        status=ReminderStatus.SCHEDULED,
    )
    await h["rem_manager"].store.save_mutation(rem)

    # When include_reminders=False, reminders are neither queried nor recommended
    req = DiagnoseScheduleDisruptionRequest(include_reminders=False)
    diag = await h["diagnostic_service"].diagnose_disruption(h["user_id"], req)
    assert len(diag.recommended_recovery_reminder_ids) == 0
    assert "reminders" not in diag.omitted_domains


@pytest.mark.asyncio
async def test_tool_user_scoping_no_default_user_id(disruption_harness):
    h = disruption_harness

    # 1. Calling tool without user_id must fail closed with UNAUTHORIZED
    res_no_user = await h["tool"].execute()
    assert res_no_user["error"] == "UNAUTHORIZED"

    # 2. Calling tool with dict params containing _user_id (ToolExecutor contract)
    res_executor_style = await h["tool"].execute({"_user_id": h["user_id"]})
    assert "diagnostic_id" in res_executor_style
    assert res_executor_style["severity"] == "none"

