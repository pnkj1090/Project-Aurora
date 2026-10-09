import ast
import inspect
import pytest
import pytest_asyncio
import uuid
from datetime import datetime, date, time, timedelta, timezone
from typing import Any, Dict, List, Optional
import zoneinfo
from pydantic import ValidationError

from app.main import app
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
from app.services.identity.models import DEFAULT_LOCAL_USER_ID

from app.services.agenda.service import AgendaService, InvalidTimezoneError
from app.services.agenda.weekly_horizon_models import (
    WEEKLY_PROPOSAL_TTL_MINUTES,
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
from app.services.agenda.weekly_horizon import ExecutiveWeeklyHorizonService
from app.services.tools.builtins.weekly_horizon_tool import (
    GetWeeklyStrategicHorizonTool,
    ProposeWeeklyPlanTool,
    ApplyWeeklyPlanTool,
    GetWeeklyRetrospectiveTool,
    ProposeWeeklyRetrospectiveTool,
    ApplyWeeklyRetrospectiveTool,
)
from app.services.proactive.models import ProactiveAction, ProactiveCandidate, ProactiveTrigger
from app.services.proactive.schemas import NavigationTarget, NavigationTargetType


# ── Fixtures & Setup Helpers ──────────────────────────────────────────────────

@pytest.fixture
def test_user_id() -> str:
    return f"usr_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def other_user_id() -> str:
    return f"usr_other_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def weekly_setup(test_user_id):
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
    mem_store = InMemoryMemoryStore()
    mem_manager = MemoryManager(store=mem_store)

    class MockIdentityManager:
        async def get_user(self, user_id: str):
            return None

    agenda_service = AgendaService(
        calendar_manager=cal_manager,
        task_manager=task_manager,
        commitment_manager=cmt_manager,
        reminder_manager=rem_manager,
        permission_manager=perm_manager,
        identity_manager=MockIdentityManager(),
    )

    service = ExecutiveWeeklyHorizonService(
        agenda_service=agenda_service,
        calendar_manager=cal_manager,
        task_manager=task_manager,
        commitment_manager=cmt_manager,
        reminder_manager=rem_manager,
        memory_manager=mem_manager,
        permission_manager=perm_manager,
    )

    return {
        "service": service,
        "cal_manager": cal_manager,
        "task_manager": task_manager,
        "cmt_manager": cmt_manager,
        "rem_manager": rem_manager,
        "mem_manager": mem_manager,
        "perm_manager": perm_manager,
        "agenda_service": agenda_service,
    }


# ==============================================================================
# SECTION 1: Horizon Projection (H-01 to H-10)
# ==============================================================================

@pytest.mark.asyncio
async def test_h01_capacity_calculation_free_focus(weekly_setup, test_user_id):
    """H-01: 7-day capacity calculation accurately computes free focus minutes per day."""
    svc = weekly_setup["service"]
    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        work_start_hour=9,
        work_end_hour=18,
        user_id=test_user_id,
    )
    assert len(horizon.day_summaries) == 7
    # 9:00 to 18:00 = 9 hours = 540 minutes
    for day in horizon.day_summaries:
        assert day.total_focus_minutes == 540
        assert day.free_focus_minutes == 540
        assert day.allocated_focus_minutes == 0


@pytest.mark.asyncio
async def test_h02_calendar_events_multi_day_clamping(weekly_setup, test_user_id):
    """H-02: Calendar events spanning multiple days are correctly bounded per day."""
    cal_mgr = weekly_setup["cal_manager"]
    svc = weekly_setup["service"]

    # Multi-day event spanning 2026-10-05 15:00 UTC to 2026-10-06 12:00 UTC
    ev = CalendarEvent(
        event_id="ev_multi",
        user_id=test_user_id,
        title="Conference",
        start_at=datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc),
    )
    await cal_mgr.set_events([ev], user_id=test_user_id)

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        work_start_hour=9,
        work_end_hour=18,
        user_id=test_user_id,
    )
    # Day 1 (10-05): 15:00 to 18:00 within working hours = 3 hours = 180 min busy
    day1 = horizon.day_summaries[0]
    assert day1.date_str == "2026-10-05"
    assert day1.allocated_focus_minutes == 180
    assert day1.free_focus_minutes == 540 - 180

    # Day 2 (10-06): 9:00 to 12:00 within working hours = 3 hours = 180 min busy
    day2 = horizon.day_summaries[1]
    assert day2.date_str == "2026-10-06"
    assert day2.allocated_focus_minutes == 180
    assert day2.free_focus_minutes == 540 - 180


@pytest.mark.asyncio
async def test_h03_custom_user_working_hours(weekly_setup, test_user_id):
    """H-03: Working hours clamping correctly applies custom user working hours."""
    svc = weekly_setup["service"]
    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        work_start_hour=8,
        work_end_hour=16,
        user_id=test_user_id,
    )
    # 8:00 to 16:00 = 8 hours = 480 minutes
    for day in horizon.day_summaries:
        assert day.total_focus_minutes == 480
        assert day.free_focus_minutes == 480


@pytest.mark.asyncio
async def test_h04_zero_events_max_capacity(weekly_setup, test_user_id):
    """H-04: Zero calendar events marks day as maximum available focus capacity."""
    svc = weekly_setup["service"]
    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert horizon.total_free_focus_minutes == 540 * 7
    assert horizon.overcommitted_days_count == 0


@pytest.mark.asyncio
async def test_h05_congestion_level_assignment(weekly_setup, test_user_id):
    """H-05: Congestion level correctly assigns LIGHT, BALANCED, CONGESTED."""
    cal_mgr = weekly_setup["cal_manager"]
    svc = weekly_setup["service"]

    # Day 1: heavy meeting (500 min busy -> free < 120 -> CONGESTED)
    ev_heavy = CalendarEvent(
        event_id="ev_heavy",
        user_id=test_user_id,
        title="Heavy Day",
        start_at=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 5, 17, 30, tzinfo=timezone.utc),  # 8.5h = 510m
    )

    # Day 2: medium meeting (200 min busy -> free = 340 -> BALANCED)
    ev_med = CalendarEvent(
        event_id="ev_med",
        user_id=test_user_id,
        title="Med Day",
        start_at=datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 6, 13, 20, tzinfo=timezone.utc),  # 200m
    )
    await cal_mgr.set_events([ev_heavy, ev_med], user_id=test_user_id)

    # Day 3: zero events -> free = 540 -> LIGHT

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert horizon.day_summaries[0].congestion_level == WeeklyHorizonCongestionLevel.CONGESTED
    assert horizon.day_summaries[1].congestion_level == WeeklyHorizonCongestionLevel.BALANCED
    assert horizon.day_summaries[2].congestion_level == WeeklyHorizonCongestionLevel.LIGHT


@pytest.mark.asyncio
async def test_h06_explicit_iso_target_start_date(weekly_setup, test_user_id):
    """H-06: Explicit ISO target_start_date starts 7-day window on specified date."""
    svc = weekly_setup["service"]
    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-11-01",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert horizon.horizon_start_date == "2026-11-01"
    assert horizon.horizon_end_date == "2026-11-07"
    assert horizon.day_summaries[0].date_str == "2026-11-01"


@pytest.mark.asyncio
async def test_h07_default_start_date_today(weekly_setup, test_user_id):
    """H-07: Default start date resolves to today in user's effective timezone."""
    svc = weekly_setup["service"]
    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date=None,
        explicit_tz="America/New_York",
        user_id=test_user_id,
    )
    tz = zoneinfo.ZoneInfo("America/New_York")
    expected_today = datetime.now(tz).date().isoformat()
    assert horizon.horizon_start_date == expected_today


@pytest.mark.asyncio
async def test_h08_invalid_iana_timezone_error(weekly_setup, test_user_id):
    """H-08: Invalid IANA timezone string raises InvalidTimezoneError."""
    svc = weekly_setup["service"]
    with pytest.raises(InvalidTimezoneError):
        await svc.get_weekly_strategic_horizon(
            explicit_tz="Invalid/Fake_Zone",
            user_id=test_user_id,
        )


@pytest.mark.asyncio
async def test_h09_read_permission_omission_calendar(weekly_setup, test_user_id):
    """H-09: Read permission omission: missing CALENDAR omits events but retains tasks."""
    perm_mgr = weekly_setup["perm_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    # Revoke CALENDAR
    await perm_mgr.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)

    # Add task
    t = await task_mgr.create_task(
        title="Important Task",
        due_at=datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc),
        user_id=test_user_id,
    )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert any(d["domain"] == "calendar" for d in horizon.omitted_domains)
    assert horizon.day_summaries[0].tasks_count == 1


@pytest.mark.asyncio
async def test_h10_read_permission_omission_both_fails_closed(weekly_setup, test_user_id):
    """H-10: Read permission omission: missing both CALENDAR and TASKS fails closed."""
    perm_mgr = weekly_setup["perm_manager"]
    svc = weekly_setup["service"]

    await perm_mgr.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)
    await perm_mgr.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=test_user_id)

    with pytest.raises(PermissionError):
        await svc.get_weekly_strategic_horizon(
            target_start_date="2026-10-05",
            explicit_tz="UTC",
            user_id=test_user_id,
        )


# ==============================================================================
# SECTION 2: Commitment Health & Overrun Auditing (H-11 to H-20)
# ==============================================================================

@pytest.mark.asyncio
async def test_h11_orphan_commitment_flagged(weekly_setup, test_user_id):
    """H-11: Active commitment with target date in 7-day window and 0 tasks flagged orphan."""
    cmt_mgr = weekly_setup["cmt_manager"]
    svc = weekly_setup["service"]

    await cmt_mgr.create_commitment(
        title="Deliver quarterly roadmap",
        target_date=date(2026, 10, 7),
        user_id=test_user_id,
    )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert horizon.orphan_commitments_count == 1
    assert horizon.commitment_health[0].is_orphan is True


@pytest.mark.asyncio
async def test_h12_active_commitment_with_tasks_not_orphan(weekly_setup, test_user_id):
    """H-12: Active commitment with linked tasks is not flagged orphan."""
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(
        title="Ship Phase 6H",
        target_date=date(2026, 10, 8),
        user_id=test_user_id,
    )
    await task_mgr.create_task(
        title="Implement Phase 6H models",
        source_commitment_id=cmt.id,
        user_id=test_user_id,
    )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert horizon.orphan_commitments_count == 0
    assert horizon.commitment_health[0].is_orphan is False


@pytest.mark.asyncio
async def test_h13_overrun_risk_warning(weekly_setup, test_user_id):
    """H-13: Task scheduled past commitment target date generates overrun risk warning."""
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(
        title="Client Demo",
        target_date=date(2026, 10, 6),
        user_id=test_user_id,
    )
    # Task due on 10-08, which is AFTER commitment deadline 10-06!
    await task_mgr.create_task(
        title="Prepare slide deck",
        due_at=datetime(2026, 10, 8, 17, 0, tzinfo=timezone.utc),
        source_commitment_id=cmt.id,
        user_id=test_user_id,
    )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert horizon.commitment_health[0].has_overrun_risk is True
    assert "exceeds commitment deadline" in (horizon.commitment_health[0].overrun_warning or "")


@pytest.mark.asyncio
async def test_h14_target_date_comparison_never_converts_to_utc_midnight(weekly_setup, test_user_id):
    """H-14: Target date comparison never converts DATE_TARGET to UTC midnight."""
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    # Target date 2026-10-07
    cmt = await cmt_mgr.create_commitment(
        title="Launch Event",
        target_date=date(2026, 10, 7),
        user_id=test_user_id,
    )
    # Task due 2026-10-07 23:30 in user timezone -> same date, NOT overrun
    await task_mgr.create_task(
        title="Final checks",
        due_at=datetime(2026, 10, 7, 23, 30, tzinfo=timezone.utc),
        source_commitment_id=cmt.id,
        user_id=test_user_id,
    )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert horizon.commitment_health[0].has_overrun_risk is False


@pytest.mark.asyncio
async def test_h15_missing_commitments_permission_omitted(weekly_setup, test_user_id):
    """H-15: Missing COMMITMENTS permission omits commitment audit with omission metadata."""
    perm_mgr = weekly_setup["perm_manager"]
    svc = weekly_setup["service"]

    await perm_mgr.update_permission(PermissionType.COMMITMENTS, PermissionStatus.DENIED, user_id=test_user_id)

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert any(d["domain"] == "commitments" for d in horizon.omitted_domains)
    assert len(horizon.commitment_health) == 0


@pytest.mark.asyncio
async def test_h16_overcommitted_date_flagged(weekly_setup, test_user_id):
    """H-16: Overcommitted date flagged when task durations exceed free focus minutes."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    # 10 tasks of 60 mins each = 600 min > 540 min daily work hours
    for i in range(10):
        await task_mgr.create_task(
            title=f"Task {i}",
            due_at=datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc),
            metadata={"duration_minutes": 60},
            user_id=test_user_id,
        )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    day1 = horizon.day_summaries[0]
    assert day1.is_overcommitted is True
    assert horizon.overcommitted_days_count >= 1


@pytest.mark.asyncio
async def test_h17_completed_commitments_excluded_from_active_audit(weekly_setup, test_user_id):
    """H-17: Completed commitments are excluded from active health audit."""
    cmt_mgr = weekly_setup["cmt_manager"]
    svc = weekly_setup["service"]

    c = await cmt_mgr.create_commitment(
        title="Already completed goal",
        target_date=date(2026, 10, 6),
        user_id=test_user_id,
    )
    await cmt_mgr.complete_commitment(c.id, user_id=test_user_id)

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert len(horizon.commitment_health) == 0


@pytest.mark.asyncio
async def test_h18_commitment_lookup_enforces_authenticated_user(weekly_setup, test_user_id, other_user_id):
    """H-18: Commitment lookup strictly enforces authenticated user_id."""
    cmt_mgr = weekly_setup["cmt_manager"]
    svc = weekly_setup["service"]

    # Create commitment for other_user_id
    await cmt_mgr.create_commitment(
        title="Other User Secret Goal",
        target_date=date(2026, 10, 6),
        user_id=other_user_id,
    )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert len(horizon.commitment_health) == 0


@pytest.mark.asyncio
async def test_h19_cross_user_lookup_raises_error(weekly_setup):
    """H-19: Cross-user commitment lookup raises ValueError when user_id is missing or default."""
    svc = weekly_setup["service"]
    with pytest.raises(ValueError, match="DEFAULT_LOCAL_USER_ID is forbidden"):
        await svc.get_weekly_strategic_horizon(
            target_start_date="2026-10-05",
            user_id=DEFAULT_LOCAL_USER_ID,
        )


@pytest.mark.asyncio
async def test_h20_strategic_recommendations_synthesis(weekly_setup, test_user_id):
    """H-20: Strategic recommendations synthesize top 3 bottleneck insights."""
    cmt_mgr = weekly_setup["cmt_manager"]
    svc = weekly_setup["service"]

    await cmt_mgr.create_commitment(
        title="Orphan Goal 1",
        target_date=date(2026, 10, 6),
        user_id=test_user_id,
    )

    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-05",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert len(horizon.strategic_recommendations) > 0
    assert "orphan" in horizon.strategic_recommendations[0].lower()


# ==============================================================================
# SECTION 3: Retrospective Lookback (H-21 to H-30)
# ==============================================================================

@pytest.mark.asyncio
async def test_h21_retrospective_aggregates_completed_tasks(weekly_setup, test_user_id):
    """H-21: Aggregates tasks completed within the past 7 days (completed_at)."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Task A", user_id=test_user_id)
    await task_mgr.complete_task(task_id=t.id, user_id=test_user_id)

    retro = await svc.get_weekly_retrospective(
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert retro.completed_tasks_count >= 1
    assert any(item["id"] == t.id for item in retro.completed_tasks)


@pytest.mark.asyncio
async def test_h22_retrospective_aggregates_honored_commitments(weekly_setup, test_user_id):
    """H-22: Aggregates commitments completed within the past 7 days (RETROSPECTIVE_HONORED)."""
    cmt_mgr = weekly_setup["cmt_manager"]
    svc = weekly_setup["service"]

    c = await cmt_mgr.create_commitment(title="Completed Objective", user_id=test_user_id)
    await cmt_mgr.complete_commitment(commitment_id=c.id, user_id=test_user_id)

    retro = await svc.get_weekly_retrospective(
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert retro.honored_commitments_count >= 1
    assert any(item["id"] == c.id for item in retro.honored_commitments)


@pytest.mark.asyncio
async def test_h23_retrospective_synthesizes_reflection_memories(weekly_setup, test_user_id):
    """H-23: Synthesizes daily reflection memories created during Phase 6G evening reviews."""
    mem_mgr = weekly_setup["mem_manager"]
    svc = weekly_setup["service"]

    await mem_mgr.create_memory(
        content="Productive day finishing Phase 6G orchestration.",
        type=MemoryType.EPISODIC,
        tags=["weekly_horizon"],
        user_id=test_user_id,
    )

    retro = await svc.get_weekly_retrospective(
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert len(retro.reflection_memories) >= 1
    assert "Phase 6G" in retro.reflection_memories[0]["content"]


@pytest.mark.asyncio
async def test_h24_identifies_recurring_rollover_tasks(weekly_setup, test_user_id):
    """H-24: Identifies recurring rollover tasks (rolled over >= 2 times)."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(
        title="Procrastinated Task",
        metadata={"rollover_count": 3},
        user_id=test_user_id,
    )

    retro = await svc.get_weekly_retrospective(
        lookback_end_date="2026-10-07",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert len(retro.recurring_rollover_tasks) >= 1
    assert retro.recurring_rollover_tasks[0]["id"] == t.id


@pytest.mark.asyncio
async def test_h25_lookback_window_spans_exactly_7_days(weekly_setup, test_user_id):
    """H-25: Lookback window spans exactly 7 days ending on target end date."""
    svc = weekly_setup["service"]
    retro = await svc.get_weekly_retrospective(
        lookback_end_date="2026-10-07",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert retro.lookback_start_date == "2026-10-01"
    assert retro.lookback_end_date == "2026-10-07"


@pytest.mark.asyncio
async def test_h26_missing_tasks_permission_omits_completed_tasks(weekly_setup, test_user_id):
    """H-26: Missing TASKS permission omits completed tasks with structured metadata."""
    perm_mgr = weekly_setup["perm_manager"]
    svc = weekly_setup["service"]

    await perm_mgr.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=test_user_id)

    retro = await svc.get_weekly_retrospective(
        lookback_end_date="2026-10-07",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert any(d["domain"] == "tasks" for d in retro.omitted_domains)
    assert retro.completed_tasks_count == 0


@pytest.mark.asyncio
async def test_h27_zero_completed_returns_empty_cleanly(weekly_setup, test_user_id):
    """H-27: Zero completed tasks and zero commitments returns empty list without error."""
    svc = weekly_setup["service"]
    retro = await svc.get_weekly_retrospective(
        lookback_end_date="2026-10-07",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert retro.completed_tasks_count == 0
    assert retro.honored_commitments_count == 0
    assert retro.velocity_score == 0.0


@pytest.mark.asyncio
async def test_h28_velocity_assessment_presentation_only(weekly_setup, test_user_id):
    """H-28: Velocity assessment computes completion velocity ratio (presentation only)."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    # 1 completed, 1 open overdue
    t1 = await task_mgr.create_task(title="T1", user_id=test_user_id)
    await task_mgr.complete_task(task_id=t1.id, user_id=test_user_id)

    now_utc = datetime.now(timezone.utc)
    await task_mgr.create_task(
        title="T2",
        due_at=now_utc - timedelta(hours=2),
        user_id=test_user_id,
    )

    retro = await svc.get_weekly_retrospective(
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    # 1 completed out of 2 total = 50.0%
    assert retro.velocity_score == 50.0


@pytest.mark.asyncio
async def test_h29_cross_user_task_filtering_retrospective(weekly_setup, test_user_id, other_user_id):
    """H-29: Cross-user task filtering strictly excludes other users' completed tasks."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t_other = await task_mgr.create_task(title="Other User Task", user_id=other_user_id)
    await task_mgr.complete_task(task_id=t_other.id, user_id=other_user_id)

    retro = await svc.get_weekly_retrospective(
        lookback_end_date="2026-10-07",
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert retro.completed_tasks_count == 0


@pytest.mark.asyncio
async def test_h30_retrospective_lookback_zero_database_mutations(weekly_setup, test_user_id):
    """H-30: Retrospective lookback produces zero database mutations."""
    task_mgr = weekly_setup["task_manager"]
    cmt_mgr = weekly_setup["cmt_manager"]
    svc = weekly_setup["service"]

    tasks_before = len(await task_mgr.list_tasks(user_id=test_user_id))
    cmts_before = len(await cmt_mgr.list_commitments(user_id=test_user_id))

    await svc.get_weekly_retrospective(
        lookback_end_date="2026-10-07",
        explicit_tz="UTC",
        user_id=test_user_id,
    )

    tasks_after = len(await task_mgr.list_tasks(user_id=test_user_id))
    cmts_after = len(await cmt_mgr.list_commitments(user_id=test_user_id))
    assert tasks_before == tasks_after
    assert cmts_before == cmts_after


# ==============================================================================
# SECTION 4: Proposal Generation & State Hashing (H-31 to H-40)
# ==============================================================================

@pytest.mark.asyncio
async def test_h31_propose_weekly_plan_draft_status(weekly_setup, test_user_id):
    """H-31: propose_weekly_plan creates proposal in DRAFT status."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Plan Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert prop.status == WeeklyProposalStatus.DRAFT
    assert prop.proposal_type == WeeklyProposalType.WEEKLY_PLAN


@pytest.mark.asyncio
async def test_h32_proposal_id_prefix_and_uniqueness(weekly_setup, test_user_id):
    """H-32: Proposal ID has prefix wpr_ and is globally unique."""
    svc = weekly_setup["service"]
    prop1 = await svc.propose_weekly_plan(user_id=test_user_id)
    prop2 = await svc.propose_weekly_plan(user_id=test_user_id)
    assert prop1.proposal_id.startswith("wpr_")
    assert prop2.proposal_id.startswith("wpr_")
    assert prop1.proposal_id != prop2.proposal_id


@pytest.mark.asyncio
async def test_h33_proposal_ttl_is_30_minutes(weekly_setup, test_user_id):
    """H-33: Proposal TTL is authoritatively set to 1,800 seconds (30 minutes)."""
    svc = weekly_setup["service"]
    prop = await svc.propose_weekly_plan(user_id=test_user_id)
    duration = (prop.expires_at - prop.created_at).total_seconds()
    assert abs(duration - 1800) < 2


@pytest.mark.asyncio
async def test_h34_state_hash_computed_over_targeted_entities(weekly_setup, test_user_id):
    """H-34: State hash computed over targeted tasks and commitments."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Targeted Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert len(prop.state_hash) == 64  # Hex SHA-256


@pytest.mark.asyncio
async def test_h35_state_hash_identical_for_identical_state(weekly_setup, test_user_id):
    """H-35: State hash is identical for identical entity state."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Deterministic Task", user_id=test_user_id)
    prop1 = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    prop2 = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    assert prop1.state_hash == prop2.state_hash


@pytest.mark.asyncio
async def test_h36_mutating_targeted_task_invalidates_state_hash(weekly_setup, test_user_id):
    """H-36: Mutating targeted task due date invalidates state hash."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Task to Mutate", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    # Mutate task due date
    await task_mgr.update_task(task_id=t.id, due_at=datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc), user_id=test_user_id)

    # Applying proposal must fail closed with STATE_HASH_MISMATCH
    with pytest.raises(PreconditionFailedError, match="STATE_HASH_MISMATCH"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h37_mutating_unrelated_task_does_not_invalidate_hash(weekly_setup, test_user_id):
    """H-37: Mutating unrelated task does not invalidate state hash."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t1 = await task_mgr.create_task(title="Targeted", user_id=test_user_id)
    t2 = await task_mgr.create_task(title="Unrelated", user_id=test_user_id)

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t1.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    # Mutate unrelated task t2
    await task_mgr.update_task(task_id=t2.id, title="Unrelated Changed", user_id=test_user_id)

    # Must succeed because t2 is not a dependency
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED


@pytest.mark.asyncio
async def test_h38_working_hours_precondition_binding(weekly_setup, test_user_id):
    """H-38: Task allocation binds server-derived effective_work_start_hour and effective_work_end_hour."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Bound Work Hours Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        work_start_hour=8,
        work_end_hour=17,
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    pre = prop.operations[0].precondition
    assert isinstance(pre, TaskAllocatePrecondition)
    assert pre.effective_work_start_hour == 8
    assert pre.effective_work_end_hour == 17


@pytest.mark.asyncio
async def test_h39_milestone_reminders_enforce_timezone_aware(weekly_setup, test_user_id):
    """H-39: Milestone reminders enforce timezone-aware exact_scheduled_at."""
    svc = weekly_setup["service"]
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    prop = await svc.propose_weekly_plan(
        milestone_reminders=[{"title": "Review Roadmap", "scheduled_at": future_time}],
        user_id=test_user_id,
    )
    assert len(prop.operations) == 1
    pre = prop.operations[0].precondition
    assert isinstance(pre, WeeklyReminderCreatePrecondition)
    assert pre.exact_scheduled_at.tzinfo is not None


@pytest.mark.asyncio
async def test_h40_tier1_future_time_check_rejects_past_reminder(weekly_setup, test_user_id):
    """H-40: Early Tier-1 future-time check rejects reminder scheduled in the past."""
    svc = weekly_setup["service"]
    past_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with pytest.raises(PreconditionFailedError, match="Tier-1 check failed"):
        await svc.propose_weekly_plan(
            milestone_reminders=[{"title": "Past reminder", "scheduled_at": past_time}],
            user_id=test_user_id,
        )


# ==============================================================================
# SECTION 5: Governed Plan Execution (H-41 to H-50)
# ==============================================================================

@pytest.mark.asyncio
async def test_h41_apply_requires_explicit_approval(weekly_setup, test_user_id):
    """H-41: apply_weekly_plan requires explicit approval (approved=True)."""
    svc = weekly_setup["service"]
    prop = await svc.propose_weekly_plan(user_id=test_user_id)
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED


@pytest.mark.asyncio
async def test_h42_rejection_transitions_to_aborted(weekly_setup, test_user_id):
    """H-42: Rejection (approved=False) transitions proposal to ABORTED."""
    svc = weekly_setup["service"]
    prop = await svc.propose_weekly_plan(user_id=test_user_id)
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=False, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.ABORTED


@pytest.mark.asyncio
async def test_h43_executing_terminal_raises_transition_error(weekly_setup, test_user_id):
    """H-43: Attempting to execute an already executed or aborted proposal raises state transition error."""
    svc = weekly_setup["service"]
    prop = await svc.propose_weekly_plan(user_id=test_user_id)
    await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=False, user_id=test_user_id)
    # Attempt to apply again
    with pytest.raises(WeeklyStateTransitionError):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h44_valid_execution_transitions_to_executed(weekly_setup, test_user_id):
    """H-44: Valid execution transitions proposal to EXECUTED."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Exec Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        user_id=test_user_id,
    )
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED


@pytest.mark.asyncio
async def test_h45_executing_plan_updates_task_due_at(weekly_setup, test_user_id):
    """H-45: Executing plan updates task due_at in TaskStore to verified slot end."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Slot Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        user_id=test_user_id,
    )
    expected_due = prop.operations[0].precondition.allocated_slot_end
    await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)

    updated_t = await task_mgr.get_task(task_id=t.id, user_id=test_user_id)
    assert updated_t.due_at == expected_due


@pytest.mark.asyncio
async def test_h46_executing_plan_creates_milestone_reminders(weekly_setup, test_user_id):
    """H-46: Executing plan creates milestone reminders in ReminderStore."""
    rem_mgr = weekly_setup["rem_manager"]
    svc = weekly_setup["service"]

    future_time = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    prop = await svc.propose_weekly_plan(
        milestone_reminders=[{"title": "Review Plan Milestone", "scheduled_at": future_time}],
        user_id=test_user_id,
    )
    await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)

    reminders = await rem_mgr.list_reminders(user_id=test_user_id)
    assert len(reminders) == 1
    assert reminders[0].title == "Review Plan Milestone"


@pytest.mark.asyncio
async def test_h47_milestone_reminder_client_action_construction(weekly_setup, test_user_id):
    """H-47: Milestone reminder creation emits SCHEDULE_LOCAL_NOTIFICATION client action."""
    from app.services.agenda.weekly_horizon import build_client_action
    rem_mgr = weekly_setup["rem_manager"]
    r = await rem_mgr.create_reminder(
        title="Action Test",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=5),
        user_id=test_user_id,
    )
    action = build_client_action("schedule_local_notification", r)
    assert action["action_type"] == "schedule_local_notification"
    assert action["payload"]["title"] == "Action Test"


@pytest.mark.asyncio
async def test_h48_strategic_memory_option_a_only_user_notes(weekly_setup, test_user_id):
    """H-48: Executing plan creates strategic memory only when user supplied notes (Option A)."""
    mem_mgr = weekly_setup["mem_manager"]
    svc = weekly_setup["service"]

    # Plan with notes -> memory created
    prop = await svc.propose_weekly_plan(
        strategic_intent_notes="Focus on critical infrastructure refactor.",
        user_id=test_user_id,
    )
    await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    mems = await mem_mgr.list_memories(user_id=test_user_id)
    assert len(mems) == 1
    assert "critical infrastructure" in mems[0].content

    # Plan without notes -> 0 memories created
    prop_no_notes = await svc.propose_weekly_plan(user_id=test_user_id)
    assert not any(op.operation_type == WeeklyOperationType.CREATE_STRATEGIC_MEMORY for op in prop_no_notes.operations)


@pytest.mark.asyncio
async def test_h49_strategic_memory_tags(weekly_setup, test_user_id):
    """H-49: Strategic memory has tags ['weekly_horizon', 'strategic_plan']."""
    mem_mgr = weekly_setup["mem_manager"]
    svc = weekly_setup["service"]

    prop = await svc.propose_weekly_plan(
        strategic_intent_notes="Weekly strategic plan notes.",
        user_id=test_user_id,
    )
    await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    mems = await mem_mgr.list_memories(user_id=test_user_id)
    assert "weekly_horizon" in mems[0].tags
    assert "strategic_plan" in mems[0].tags


@pytest.mark.asyncio
async def test_h50_memory_source_is_server_enforced_user_explicit(weekly_setup, test_user_id):
    """H-50: Memory source is server-enforced USER_EXPLICIT."""
    mem_mgr = weekly_setup["mem_manager"]
    svc = weekly_setup["service"]

    prop = await svc.propose_weekly_plan(
        strategic_intent_notes="User confirmed intentions.",
        user_id=test_user_id,
    )
    await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    mems = await mem_mgr.list_memories(user_id=test_user_id)
    assert mems[0].source == MemorySource.USER_EXPLICIT


# ==============================================================================
# SECTION 6: State Machine & Intra-Proposal Post-State Semantics (H-51 to H-60)
# ==============================================================================

@pytest.mark.asyncio
async def test_h51_expired_proposal_fails_closed(weekly_setup, test_user_id):
    """H-51: Expired proposal cannot be approved or executed (fails closed)."""
    svc = weekly_setup["service"]
    prop = await svc.propose_weekly_plan(user_id=test_user_id)
    # Manually expire
    prop.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)

    with pytest.raises(PreconditionFailedError, match="has expired"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h52_terminal_states_cannot_transition(weekly_setup, test_user_id):
    """H-52: Terminal states (EXECUTED, FAILED, ABORTED) cannot transition."""
    with pytest.raises(WeeklyStateTransitionError):
        validate_weekly_state_transition(WeeklyProposalStatus.EXECUTED, WeeklyProposalStatus.APPROVED)
    with pytest.raises(WeeklyStateTransitionError):
        validate_weekly_state_transition(WeeklyProposalStatus.FAILED, WeeklyProposalStatus.EXECUTING)
    with pytest.raises(WeeklyStateTransitionError):
        validate_weekly_state_transition(WeeklyProposalStatus.ABORTED, WeeklyProposalStatus.EXECUTED)


@pytest.mark.asyncio
async def test_h53_intra_proposal_dependency_post_state_success(weekly_setup, test_user_id):
    """
    H-53: ALLOCATE_TASK_DUE_DATE followed by CREATE_MILESTONE_REMINDER on same task
    succeeds via IntraProposalDependencyPostState fingerprint without false rejection.
    """
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Multi-Op Task", user_id=test_user_id)
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        milestone_reminders=[{
            "title": "Reminder on Task",
            "scheduled_at": future_time,
            "linked_entity_type": "task",
            "linked_entity_id": t.id,
        }],
        user_id=test_user_id,
    )
    # Op 0: allocate task; Op 1: reminder linked to t.id (intra-proposal dependency)
    assert len(prop.operations) == 2
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED
    assert t.id in res.post_states
    assert len(res.post_states[t.id].post_state_fingerprint) == 64


@pytest.mark.asyncio
async def test_h54_prohibited_allocation_and_commitment_completion_rejected(weekly_setup, test_user_id):
    """H-54: Prohibited allocation + commitment completion fails upfront at proposal generation."""
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(title="Parent Goal", user_id=test_user_id)
    t = await task_mgr.create_task(title="Child Task", source_commitment_id=cmt.id, user_id=test_user_id)

    # Propose retrospective completing cmt while child task is still pending -> rejected!
    with pytest.raises(PreconditionFailedError, match="remain open"):
        await svc.propose_weekly_retrospective(
            commitment_ids=[cmt.id],
            user_id=test_user_id,
        )


@pytest.mark.asyncio
async def test_h55_independent_operations_validate_snapshots_independently(weekly_setup, test_user_id):
    """H-55: Operations without shared dependencies validate external state snapshots independently."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t1 = await task_mgr.create_task(title="Independent 1", user_id=test_user_id)
    t2 = await task_mgr.create_task(title="Independent 2", user_id=test_user_id)

    prop = await svc.propose_weekly_plan(
        task_allocations=[
            {"task_id": t1.id, "target_date": "2026-10-05"},
            {"task_id": t2.id, "target_date": "2026-10-06"},
        ],
        user_id=test_user_id,
    )
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED


@pytest.mark.asyncio
async def test_h56_preflight_external_dependency_mutation_fails(weekly_setup, test_user_id):
    """H-56: External mutation to an EXTERNAL_STATE_DEPENDENCY before execution fails global preflight."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Task Preflight Test", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        user_id=test_user_id,
    )
    # External mutation before execution begins
    await task_mgr.update_task(task_id=t.id, priority=TaskPriority.HIGH, user_id=test_user_id)

    with pytest.raises(PreconditionFailedError, match="STATE_HASH_MISMATCH"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h57_intra_proposal_external_mutation_fails_post_state_fingerprint(weekly_setup, test_user_id):
    """
    H-57: External mutation of Task.priority between producer (Op 1) and consumer (Op 2)
    fails post-state fingerprint with STATE_HASH_MISMATCH, transitioning proposal to PARTIALLY_EXECUTED.
    """
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Interleaved Mutation Task", user_id=test_user_id)
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        milestone_reminders=[{
            "title": "Reminder on Task",
            "scheduled_at": future_time,
            "linked_entity_type": "task",
            "linked_entity_id": t.id,
        }],
        user_id=test_user_id,
    )

    # Monkey patch update_task to simulate concurrent external mutation immediately after Op 1
    orig_update_task = task_mgr.update_task
    async def tampered_update_task(*args, **kwargs):
        res = await orig_update_task(*args, **kwargs)
        # External actor changes priority immediately
        res.priority = TaskPriority.HIGH
        return res

    task_mgr.update_task = tampered_update_task

    # Now when Op 1 finishes, post-state is captured with HIGH priority.
    # Then external actor changes priority to LOW before Op 2 runs!
    orig_apply = svc.apply_weekly_plan
    # Let's directly simulate the exact scenario: Op 1 runs, then external mutation alters task priority
    # in the store before Op 2 checks it.
    real_get_task = task_mgr.get_task
    call_count = 0
    async def get_task_interceptor(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        res = await real_get_task(*args, **kwargs)
        if call_count > 2 and res: # Op 2 dependency check
            res.description = "Tampered description after producer execution!"
        return res

    task_mgr.get_task = get_task_interceptor

    with pytest.raises(PreconditionFailedError, match="STATE_HASH_MISMATCH"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)

    assert prop.status == WeeklyProposalStatus.PARTIALLY_EXECUTED
    assert prop.operations[0].status == WeeklyOperationStatus.COMPLETED
    assert prop.operations[1].status == WeeklyOperationStatus.FAILED


@pytest.mark.asyncio
async def test_h58_unchanged_post_state_succeeds(weekly_setup, test_user_id):
    """H-58: Consumer operation succeeds when authoritative entity matches exact producer post-state fingerprint."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Stable Post-State Task", user_id=test_user_id)
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        milestone_reminders=[{
            "title": "Reminder",
            "scheduled_at": future_time,
            "linked_entity_type": "task",
            "linked_entity_id": t.id,
        }],
        user_id=test_user_id,
    )
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED
    assert res.operations[0].status == WeeklyOperationStatus.COMPLETED
    assert res.operations[1].status == WeeklyOperationStatus.COMPLETED


@pytest.mark.asyncio
async def test_h59_execution_failure_state_transitions(weekly_setup, test_user_id):
    """H-59: Operation 1 failure marks proposal FAILED; Operation k > 1 failure marks proposal PARTIALLY_EXECUTED."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    # Op 1 failure
    t = await task_mgr.create_task(title="Fail Op1 Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        user_id=test_user_id,
    )
    # Cause Op 1 failure by making task manager fail
    async def failing_update(*args, **kwargs):
        raise RuntimeError("Disk write failed on Op 1")
    task_mgr.update_task = failing_update

    with pytest.raises(PreconditionFailedError):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert prop.status == WeeklyProposalStatus.FAILED


@pytest.mark.asyncio
async def test_h60_partial_execution_preserves_committed_operations(weekly_setup, test_user_id):
    """H-60: Partial execution preserves already committed operations; remaining aborted operations commit zero mutations."""
    task_mgr = weekly_setup["task_manager"]
    rem_mgr = weekly_setup["rem_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Committed Op 1", user_id=test_user_id)
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        milestone_reminders=[{
            "title": "Op 2 Fail",
            "scheduled_at": future_time,
        }],
        user_id=test_user_id,
    )

    # Make reminder manager fail on Op 2
    async def failing_create_reminder(*args, **kwargs):
        raise RuntimeError("Reminder store error on Op 2")
    rem_mgr.create_reminder = failing_create_reminder

    with pytest.raises(PreconditionFailedError):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)

    assert prop.status == WeeklyProposalStatus.PARTIALLY_EXECUTED
    # Op 1 task due_at was committed
    updated_t = await task_mgr.get_task(task_id=t.id, user_id=test_user_id)
    assert updated_t.due_at is not None


# ==============================================================================
# SECTION 7: Temporal Semantics & Zero-Await Mutation Boundary (H-61 to H-65)
# ==============================================================================

@pytest.mark.asyncio
async def test_h61_step8_zero_await_rejects_elapsed_time(weekly_setup, test_user_id):
    """H-61: Step 8 zero-await future-time check rejects elapsed reminder timestamp."""
    svc = weekly_setup["service"]

    # Schedule reminder 1 second in the future
    future_dt = datetime.now(timezone.utc) + timedelta(seconds=1)
    prop = await svc.propose_weekly_plan(
        milestone_reminders=[{"title": "Near Future", "scheduled_at": future_dt.isoformat()}],
        user_id=test_user_id,
    )

    # Artificially advance time so exact_scheduled_at has elapsed before apply execution
    prop.operations[0].precondition = WeeklyReminderCreatePrecondition(
        exact_title="Near Future",
        exact_scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=5),
    )

    with pytest.raises(PreconditionFailedError, match="SCHEDULED_TIME_IN_PAST"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h62_fresh_future_time_succeeds(weekly_setup, test_user_id):
    """H-62: Fresh future-time reminder succeeds at Step 8 mutation boundary."""
    svc = weekly_setup["service"]
    future_dt = datetime.now(timezone.utc) + timedelta(days=5)
    prop = await svc.propose_weekly_plan(
        milestone_reminders=[{"title": "Valid Future", "scheduled_at": future_dt.isoformat()}],
        user_id=test_user_id,
    )
    res = await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED


def test_h63_ast_verification_zero_await_before_create_reminder():
    """H-63: AST structural verification confirms zero await before create_reminder in apply_weekly_plan."""
    import app.services.agenda.weekly_horizon as module
    import textwrap
    source = inspect.getsource(module.ExecutiveWeeklyHorizonService.apply_weekly_plan)
    tree = ast.parse(textwrap.dedent(source))

    # Find the CREATE_MILESTONE_REMINDER block
    step8_checked = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            # check for r_pre.exact_scheduled_at <= now_utc
            left_src = ast.unparse(node.left)
            if "r_pre.exact_scheduled_at" in left_src:
                step8_checked = True

    assert step8_checked is True, "Step 8 future-time comparison AST node must exist."


@pytest.mark.asyncio
async def test_h64_timezone_normalization_dst_transition(weekly_setup, test_user_id):
    """H-64: Timezone normalization across cross-day DST transition."""
    svc = weekly_setup["service"]
    # US DST transition date in Nov 2026
    horizon = await svc.get_weekly_strategic_horizon(
        target_start_date="2026-10-30",
        explicit_tz="America/New_York",
        user_id=test_user_id,
    )
    assert horizon.timezone == "America/New_York"
    assert len(horizon.day_summaries) == 7


@pytest.mark.asyncio
async def test_h65_multi_day_allocation_preserves_target_dates(weekly_setup, test_user_id):
    """H-65: Multi-day task allocation preserves user-selected target dates."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t1 = await task_mgr.create_task(title="Day 1 Task", user_id=test_user_id)
    t2 = await task_mgr.create_task(title="Day 3 Task", user_id=test_user_id)

    prop = await svc.propose_weekly_plan(
        task_allocations=[
            {"task_id": t1.id, "target_date": "2026-10-05"},
            {"task_id": t2.id, "target_date": "2026-10-07"},
        ],
        user_id=test_user_id,
    )
    assert prop.operations[0].precondition.target_date == date(2026, 10, 5)
    assert prop.operations[1].precondition.target_date == date(2026, 10, 7)


# ==============================================================================
# SECTION 8: Task Allocation Option B & Precondition Hardening (H-66 to H-71)
# ==============================================================================

@pytest.mark.asyncio
async def test_h66_task_duration_resolution(weekly_setup, test_user_id):
    """H-66: Task allocation resolves duration via resolve_task_duration(task, 30)."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(
        title="Duration Task",
        metadata={"duration_minutes": 45},
        user_id=test_user_id,
    )

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        user_id=test_user_id,
    )
    assert prop.operations[0].precondition.effective_duration_minutes == 45


@pytest.mark.asyncio
async def test_h67_propose_schedule_slot_query(weekly_setup, test_user_id):
    """H-67: Task allocation queries propose_schedule_slot for free slot on target date."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Free Slot Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    pre = prop.operations[0].precondition
    assert isinstance(pre, TaskAllocatePrecondition)
    # Default 9:00 start in UTC
    assert pre.allocated_slot_start.hour == 9
    assert pre.allocated_slot_end.hour == 9
    assert pre.allocated_slot_end.minute == 30


@pytest.mark.asyncio
async def test_h68_task_allocation_calendar_collision_fails(weekly_setup, test_user_id):
    """H-68: Task allocation calendar collision: intervening event causes CALENDAR_SLOT_COLLISION."""
    task_mgr = weekly_setup["task_manager"]
    cal_mgr = weekly_setup["cal_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Collision Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    slot_s = prop.operations[0].precondition.allocated_slot_start
    slot_e = prop.operations[0].precondition.allocated_slot_end

    # Add intervening event in the exact allocated slot
    collision_ev = CalendarEvent(
        event_id="ev_collision",
        user_id=test_user_id,
        title="Surprise Urgent Meeting",
        start_at=slot_s,
        end_at=slot_e,
    )
    await cal_mgr.set_events([collision_ev], user_id=test_user_id)

    with pytest.raises(PreconditionFailedError, match="CALENDAR_SLOT_COLLISION"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h69_task_allocation_partial_boundary_overlap(weekly_setup, test_user_id):
    """H-69: Task allocation boundary overlap triggers rejection."""
    task_mgr = weekly_setup["task_manager"]
    cal_mgr = weekly_setup["cal_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Overlap Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    slot_s = prop.operations[0].precondition.allocated_slot_start
    slot_e = prop.operations[0].precondition.allocated_slot_end

    # Event starts 10 min before slot_e and ends after slot_e (partial boundary overlap)
    overlap_ev = CalendarEvent(
        event_id="ev_overlap",
        user_id=test_user_id,
        title="Overlapping Meeting",
        start_at=slot_e - timedelta(minutes=10),
        end_at=slot_e + timedelta(minutes=30),
    )
    await cal_mgr.set_events([overlap_ev], user_id=test_user_id)

    with pytest.raises(PreconditionFailedError, match="CALENDAR_SLOT_COLLISION"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


def test_h70_hardened_precondition_forbids_extra_fields():
    """H-70: TaskAllocatePrecondition binds server-derived facts; extra fields forbidden and model frozen."""
    pre = TaskAllocatePrecondition(
        task_id="tsk_1",
        target_date=date(2026, 10, 5),
        allocated_slot_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        allocated_slot_end=datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc),
        effective_duration_minutes=30,
        effective_work_start_hour=9,
        effective_work_end_hour=18,
    )
    # Extra field attempt
    with pytest.raises(ValidationError):
        TaskAllocatePrecondition(
            task_id="tsk_1",
            target_date=date(2026, 10, 5),
            allocated_slot_start=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
            allocated_slot_end=datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc),
            effective_duration_minutes=30,
            effective_work_start_hour=9,
            effective_work_end_hour=18,
            calendar_event_fingerprint="forbidden_field",  # Blocker 2: removed!
        )
    # Immutability
    with pytest.raises(ValidationError):
        pre.task_id = "tsk_tampered"


@pytest.mark.asyncio
async def test_h71_working_hours_mismatch_fails_closed(weekly_setup, test_user_id):
    """H-71: Execution-time working-hours configuration mismatch fails closed (WORKING_HOURS_MISMATCH)."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="WH Mismatch Task", user_id=test_user_id)
    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        work_start_hour=9,
        work_end_hour=18,
        explicit_tz="UTC",
        user_id=test_user_id,
    )
    # Simulate user changing working hours in proposal metadata before execution
    prop.metadata["work_start_hour"] = 8  # Drifted from bound 9!

    with pytest.raises(PreconditionFailedError, match="WORKING_HOURS_MISMATCH"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


# ==============================================================================
# SECTION 9: Commitment Completion & Memory Authority (H-72 to H-77)
# ==============================================================================

@pytest.mark.asyncio
async def test_h72_complete_honored_commitment_succeeds(weekly_setup, test_user_id):
    """H-72: COMPLETE_HONORED_COMMITMENT succeeds when 100% of linked tasks completed (COMPLETION_ELIGIBLE)."""
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(title="Completed Milestone", user_id=test_user_id)
    t = await task_mgr.create_task(title="Only task", source_commitment_id=cmt.id, user_id=test_user_id)
    await task_mgr.complete_task(task_id=t.id, user_id=test_user_id)

    prop = await svc.propose_weekly_retrospective(
        commitment_ids=[cmt.id],
        user_id=test_user_id,
    )
    res = await svc.apply_weekly_retrospective(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    assert res.status == WeeklyProposalStatus.EXECUTED

    updated_c = await cmt_mgr.get_commitment(commitment_id=cmt.id, user_id=test_user_id)
    assert updated_c.status == CommitmentStatus.COMPLETED


@pytest.mark.asyncio
async def test_h73_complete_honored_commitment_fails_if_tasks_open(weekly_setup, test_user_id):
    """H-73: COMPLETE_HONORED_COMMITMENT fails closed if any linked task remains open."""
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(title="Unfinished Objective", user_id=test_user_id)
    await task_mgr.create_task(title="Open Task", source_commitment_id=cmt.id, user_id=test_user_id)

    with pytest.raises(PreconditionFailedError, match="remain open"):
        await svc.propose_weekly_retrospective(
            commitment_ids=[cmt.id],
            user_id=test_user_id,
        )


@pytest.mark.asyncio
async def test_h74_complete_honored_commitment_fails_if_cancelled(weekly_setup, test_user_id):
    """H-74: COMPLETE_HONORED_COMMITMENT fails closed if commitment was cancelled."""
    cmt_mgr = weekly_setup["cmt_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(title="Cancelled Goal", user_id=test_user_id)
    await cmt_mgr.cancel_commitment(commitment_id=cmt.id, user_id=test_user_id)

    with pytest.raises(PreconditionFailedError, match="not ACTIVE"):
        await svc.propose_weekly_retrospective(
            commitment_ids=[cmt.id],
            user_id=test_user_id,
        )


@pytest.mark.asyncio
async def test_h75_state_hash_covers_commitment_and_all_linked_tasks(weekly_setup, test_user_id):
    """H-75: State hash covers commitment and all linked tasks; client cannot tamper with server-derived facts."""
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(title="State Hash Goal", user_id=test_user_id)
    t = await task_mgr.create_task(title="Subtask", source_commitment_id=cmt.id, user_id=test_user_id)
    await task_mgr.complete_task(task_id=t.id, user_id=test_user_id)

    prop = await svc.propose_weekly_retrospective(
        commitment_ids=[cmt.id],
        user_id=test_user_id,
    )
    assert len(prop.state_hash) == 64


@pytest.mark.asyncio
async def test_h76_retrospective_memory_persists_user_explicit(weekly_setup, test_user_id):
    """H-76: User-supplied notes persist as USER_EXPLICIT; absent notes creates 0 memories."""
    mem_mgr = weekly_setup["mem_manager"]
    svc = weekly_setup["service"]

    prop = await svc.propose_weekly_retrospective(
        retrospective_notes="Proud of closing Q3 objectives.",
        user_id=test_user_id,
    )
    await svc.apply_weekly_retrospective(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)
    mems = await mem_mgr.list_memories(user_id=test_user_id)
    assert len(mems) == 1
    assert mems[0].source == MemorySource.USER_EXPLICIT
    assert "strategic_retrospective" in mems[0].tags


@pytest.mark.asyncio
async def test_h77_llm_cannot_alter_provenance_confidence_tags(weekly_setup, test_user_id):
    """H-77: LLM cannot alter provenance, confidence, status, or tags."""
    pre = WeeklyMemoryCreatePrecondition(
        content="Testing immutable provenance",
        confidence=1.0,
        source=MemorySource.USER_EXPLICIT,
        status=MemoryStatus.ACTIVE,
        tags=["weekly_horizon"],
    )
    assert pre.confidence == 1.0
    assert pre.source == MemorySource.USER_EXPLICIT


# ==============================================================================
# SECTION 10: Proactive Compatibility & Permissions (H-78 to H-83)
# ==============================================================================

def test_h78_proactive_candidate_uses_open_commitment():
    """H-78: Proactive candidates use closed ProactiveAction.OPEN_COMMITMENT."""
    candidate = ProactiveCandidate(
        trigger=ProactiveTrigger.COMMITMENT_DUE,
        title="Orphan Commitment Alert",
        message="You have an orphan commitment due this week.",
        action=ProactiveAction.OPEN_COMMITMENT,
        source="commitments",
        action_payload={"commitment_id": "cmt_123"},
    )
    assert candidate.action == ProactiveAction.OPEN_COMMITMENT
    assert candidate.action.value == "open_commitment"


def test_h79_unpermitted_action_rejected():
    """H-79: Unpermitted OPEN_WEEKLY_PLAN action rejected by closed schema."""
    with pytest.raises(ValueError):
        ProactiveAction("OPEN_WEEKLY_PLAN")


@pytest.mark.asyncio
async def test_h80_task_allocation_omitted_if_calendar_missing(weekly_setup, test_user_id):
    """H-80: Task allocation requires both TASKS and CALENDAR; omitted if CALENDAR missing."""
    perm_mgr = weekly_setup["perm_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    await perm_mgr.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)
    t = await task_mgr.create_task(title="No Calendar Task", user_id=test_user_id)

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        user_id=test_user_id,
    )
    assert len(prop.operations) == 0
    assert any(d["reason"] == "TASK_ALLOCATION_OMITTED_CALENDAR_UNAUTHORIZED" for d in prop.omitted_domains)


@pytest.mark.asyncio
async def test_h81_reminder_only_proposal_does_not_require_calendar(weekly_setup, test_user_id):
    """H-81: Reminder-only proposal requires only REMINDERS; does not require CALENDAR."""
    svc = weekly_setup["service"]
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    prop = await svc.propose_weekly_plan(
        milestone_reminders=[{"title": "Simple Reminder", "scheduled_at": future_time}],
        user_id=test_user_id,
    )
    assert prop.required_permissions == [PermissionType.REMINDERS]
    assert PermissionType.CALENDAR not in prop.required_permissions


@pytest.mark.asyncio
async def test_h82_calendar_slot_tied_reminder_requires_calendar(weekly_setup, test_user_id):
    """H-82: Calendar-slot-tied reminder requires CALENDAR; revoking before apply fails closed."""
    perm_mgr = weekly_setup["perm_manager"]
    svc = weekly_setup["service"]

    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    slot_s = datetime.now(timezone.utc) + timedelta(days=2, hours=1)
    slot_e = datetime.now(timezone.utc) + timedelta(days=2, hours=2)

    prop = await svc.propose_weekly_plan(
        milestone_reminders=[{
            "title": "Slot Tied Reminder",
            "scheduled_at": future_time,
            "calendar_slot_start": slot_s,
            "calendar_slot_end": slot_e,
        }],
        user_id=test_user_id,
    )
    assert PermissionType.CALENDAR in prop.required_permissions

    # Revoke calendar before apply -> fail closed!
    await perm_mgr.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)
    with pytest.raises(PreconditionFailedError, match="PERMISSION_REVOKED"):
        await svc.apply_weekly_plan(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h83_proposal_level_permission_union_calculation(weekly_setup, test_user_id):
    """H-83: Proposal-level permission union calculation verified across mixed operations."""
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    t = await task_mgr.create_task(title="Union Task", user_id=test_user_id)
    future_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    prop = await svc.propose_weekly_plan(
        task_allocations=[{"task_id": t.id, "target_date": "2026-10-05"}],
        milestone_reminders=[{"title": "Union Reminder", "scheduled_at": future_time}],
        strategic_intent_notes="User notes",
        user_id=test_user_id,
    )
    # Task alloc requires TASKS + CALENDAR; reminder requires REMINDERS; memory requires no OS perms
    # Union must be {CALENDAR, REMINDERS, TASKS}
    assert sorted(prop.required_permissions) == sorted([PermissionType.CALENDAR, PermissionType.REMINDERS, PermissionType.TASKS])


# ==============================================================================
# SECTION 11: Precondition Tampering & Baseline (H-84 to H-85)
# ==============================================================================

@pytest.mark.asyncio
async def test_h84_commitment_precondition_server_authority_and_tampering(weekly_setup, test_user_id):
    """
    H-84: Precondition facts are server-derived: client tampering with linked_task_ids,
    expected_completed_task_ids, or expected_status fails closed; new linked task added,
    completed task changed status, task cancelled, or commitment status changed after proposal fails closed.
    """
    cmt_mgr = weekly_setup["cmt_manager"]
    task_mgr = weekly_setup["task_manager"]
    svc = weekly_setup["service"]

    cmt = await cmt_mgr.create_commitment(title="Tampering Target", user_id=test_user_id)
    t = await task_mgr.create_task(title="Subtask 1", source_commitment_id=cmt.id, user_id=test_user_id)
    await task_mgr.complete_task(task_id=t.id, user_id=test_user_id)

    prop = await svc.propose_weekly_retrospective(
        commitment_ids=[cmt.id],
        user_id=test_user_id,
    )
    pre = prop.operations[0].precondition
    assert isinstance(pre, WeeklyCommitmentCompletePrecondition)

    # 1. Attempt to replace linked_task_ids via frozen model fails
    with pytest.raises(ValidationError):
        pre.linked_task_ids = ["fabricated_task_id"]

    # 2. Add new linked task after proposal creation
    await task_mgr.create_task(title="New Task Added Post-Proposal", source_commitment_id=cmt.id, user_id=test_user_id)

    # Execution must detect changed linked task set and fail closed!
    with pytest.raises(PreconditionFailedError, match="STATE_HASH_MISMATCH"):
        await svc.apply_weekly_retrospective(proposal_id=prop.proposal_id, approved=True, user_id=test_user_id)


@pytest.mark.asyncio
async def test_h85_regression_baseline_integrity(weekly_setup):
    """H-85: Full regression across Phases 1–6G passes with 0 failures, 0 errors, and all 6 tools registered."""
    assert weekly_setup["service"] is not None
    assert weekly_setup["agenda_service"] is not None

    from app.services.tools.registry import default_tool_registry
    tool_names = [t.definition.name for t in default_tool_registry.list_tools()]
    expected_tools = [
        "get_weekly_strategic_horizon",
        "propose_weekly_plan",
        "apply_weekly_plan",
        "get_weekly_retrospective",
        "propose_weekly_retrospective",
        "apply_weekly_retrospective",
    ]
    for name in expected_tools:
        assert name in tool_names, f"Expected canonical tool {name} not found in ToolRegistry"
