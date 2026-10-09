import asyncio
import zoneinfo
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.agenda import (
    AgendaService,
    AgendaTemporalKind,
    AgendaItem,
    ConflictType,
    ConflictSeverity,
    InvalidTimezoneError,
    get_agenda_service_dependency,
)
from app.services.calendar import CalendarManager, CalendarEvent
from app.services.tasks import TaskManager, InMemoryTaskStore, TaskPriority, TaskStatus
from app.services.commitments import CommitmentManager, InMemoryCommitmentStore, CommitmentPriority, CommitmentStatus
from app.services.reminders import ReminderManager, InMemoryReminderStore, ReminderStatus
from app.services.permissions import (
    PermissionManager,
    InMemoryPermissionStore,
    PermissionType,
    PermissionStatus,
    get_permission_manager_dependency,
)
from app.services.identity.auth import (
    IdentityResolver,
    SQLiteCredentialStore,
    get_credential_store_dependency,
    get_identity_resolver_dependency,
    provision_credential,
)
from app.services.tools.builtins.agenda_tool import (
    GetDailyAgendaTool,
    ProposeScheduleSlotTool,
    DetectScheduleConflictsTool,
)
from app.services.proactive.evaluators.conflict_evaluator import (
    ScheduleConflictEvaluator,
    ALLOWLISTED_CONFLICT_TYPES,
)
from app.services.context.providers.agenda import AgendaContextProvider
from app.services.context.engine import ContextEngine


@pytest_asyncio.fixture
async def phase6d_environment():
    perm_store = InMemoryPermissionStore()
    perm_mgr = PermissionManager(perm_store)

    cal_mgr = CalendarManager(permission_manager=perm_mgr)
    task_store = InMemoryTaskStore()
    task_mgr = TaskManager(store=task_store)
    cmt_store = InMemoryCommitmentStore()
    cmt_mgr = CommitmentManager(store=cmt_store)
    task_mgr._commitment_manager = cmt_mgr
    rem_store = InMemoryReminderStore()
    rem_mgr = ReminderManager(store=rem_store)

    service = AgendaService(
        calendar_manager=cal_mgr,
        task_manager=task_mgr,
        commitment_manager=cmt_mgr,
        reminder_manager=rem_mgr,
        permission_manager=perm_mgr,
    )

    user_id = "usr_phase6d_test"
    for p in (PermissionType.CALENDAR, PermissionType.TASKS, PermissionType.COMMITMENTS, PermissionType.REMINDERS):
        await perm_mgr.update_permission(p, PermissionStatus.AUTHORIZED, user_id=user_id)

    return {
        "service": service,
        "cal_mgr": cal_mgr,
        "task_mgr": task_mgr,
        "cmt_mgr": cmt_mgr,
        "rem_mgr": rem_mgr,
        "perm_mgr": perm_mgr,
        "user_id": user_id,
    }


# ==============================================================================
# Invariant A: Temporal Normalization
# ==============================================================================

@pytest.mark.asyncio
async def test_a_temporal_normalization():
    # INTERVAL
    item_int = AgendaItem(
        item_id="calendar:ev_1",
        source_domain="calendar",
        source_entity_id="ev_1",
        display_title="Team Sync",
        temporal_kind=AgendaTemporalKind.INTERVAL,
        start_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
        status="scheduled",
    )
    assert item_int.temporal_kind == AgendaTemporalKind.INTERVAL
    assert item_int.instant_at is None
    assert item_int.target_date is None

    # INSTANT
    item_inst = AgendaItem(
        item_id="tasks:tsk_1",
        source_domain="tasks",
        source_entity_id="tsk_1",
        display_title="Submit Report",
        temporal_kind=AgendaTemporalKind.INSTANT,
        instant_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        status="pending",
    )
    assert item_inst.temporal_kind == AgendaTemporalKind.INSTANT
    assert item_inst.start_at is None
    assert item_inst.target_date is None

    # DATE_TARGET
    item_dt = AgendaItem(
        item_id="commitments:cmt_1",
        source_domain="commitments",
        source_entity_id="cmt_1",
        display_title="Launch Beta",
        temporal_kind=AgendaTemporalKind.DATE_TARGET,
        target_date=date(2026, 10, 15),
        status="active",
    )
    assert item_dt.temporal_kind == AgendaTemporalKind.DATE_TARGET
    assert item_dt.start_at is None
    assert item_dt.instant_at is None


# ==============================================================================
# Invariant B: Timezone & Local Date Boundaries
# ==============================================================================

@pytest.mark.asyncio
async def test_b_timezone_local_date_boundaries(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # In America/New_York (UTC-4 in Sep), 2026-09-28 00:00 is 2026-09-28 04:00 UTC
    tz_ny = await service.resolve_effective_timezone("America/New_York", user_id=user_id)
    assert str(tz_ny) == "America/New_York"

    # In Asia/Kolkata (UTC+5:30), 2026-09-28 00:00 is 2026-09-27 18:30 UTC
    tz_kol = await service.resolve_effective_timezone("Asia/Kolkata", user_id=user_id)
    assert str(tz_kol) == "Asia/Kolkata"


# ==============================================================================
# Invariant C: Deterministic Invalid Timezone Rejection
# ==============================================================================

@pytest.mark.asyncio
async def test_c_deterministic_invalid_timezone_rejection(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # Malformed IANA string raises InvalidTimezoneError
    with pytest.raises(InvalidTimezoneError):
        await service.resolve_effective_timezone("Invalid/Nowhere", user_id=user_id)

    # Raw offset strings like +05:30 must be rejected deterministically
    with pytest.raises(InvalidTimezoneError):
        await service.resolve_effective_timezone("+05:30", user_id=user_id)


# ==============================================================================
# Invariant D: Timezone Override Isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_d_timezone_override_isolation(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # Explicit override resolves for request
    tz = await service.resolve_effective_timezone("Europe/Paris", user_id=user_id)
    assert str(tz) == "Europe/Paris"

    # Subsequent request without explicit override falls back to user profile/UTC
    tz_default = await service.resolve_effective_timezone(None, user_id=user_id)
    assert str(tz_default) == "UTC"


# ==============================================================================
# Invariant E: DST Transition Behavior
# ==============================================================================

@pytest.mark.asyncio
async def test_e_dst_transition_behavior(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # US Spring forward (23-hour day): 2026-03-08
    tz_us = zoneinfo.ZoneInfo("America/New_York")
    d1 = datetime(2026, 3, 8, 0, 0, tzinfo=tz_us).astimezone(timezone.utc)
    d2 = datetime(2026, 3, 8, 23, 59, 59, 999999, tzinfo=tz_us).astimezone(timezone.utc)
    duration_hours = (d2 - d1).total_seconds() / 3600.0
    assert 22.9 < duration_hours < 23.1


# ==============================================================================
# Invariant F: Commitment DATE_TARGET Semantics
# ==============================================================================

@pytest.mark.asyncio
async def test_f_commitment_date_target_semantics():
    # Must accept strictly date, not datetime
    dt_target = date(2026, 10, 15)
    item = AgendaItem(
        item_id="commitments:cmt_123",
        source_domain="commitments",
        source_entity_id="cmt_123",
        display_title="Finish Architecture",
        temporal_kind=AgendaTemporalKind.DATE_TARGET,
        target_date=dt_target,
        status="active",
    )
    assert type(item.target_date) is date
    assert not isinstance(item.target_date, datetime)
    dumped = item.model_dump(mode="json")
    assert dumped["target_date"] == "2026-10-15"


# ==============================================================================
# Invariant G: Public / Internal Identifier Separation
# ==============================================================================

@pytest.mark.asyncio
async def test_g_public_internal_identifier_separation(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # Populate snapshot with public event ID
    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="cal_event_pub_999",
            title="Design Review",
            start_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc),
            user_id=user_id,
        )
    ], user_id=user_id)

    agenda = await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    item = agenda.items[0]
    assert item.item_id == "calendar:cal_event_pub_999"
    assert item.source_entity_id == "cal_event_pub_999"
    assert "sqlite" not in item.item_id.lower()


# ==============================================================================
# Invariant H: Pydantic Validation of Temporal Invariants
# ==============================================================================

@pytest.mark.asyncio
async def test_h_temporal_kind_pydantic_validation():
    # INTERVAL missing end_at
    with pytest.raises(ValueError):
        AgendaItem(
            item_id="cal:1",
            source_domain="calendar",
            source_entity_id="1",
            display_title="Bad Interval",
            temporal_kind=AgendaTemporalKind.INTERVAL,
            start_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
            status="scheduled",
        )

    # INSTANT with start_at populated
    with pytest.raises(ValueError):
        AgendaItem(
            item_id="tasks:1",
            source_domain="tasks",
            source_entity_id="1",
            display_title="Bad Instant",
            temporal_kind=AgendaTemporalKind.INSTANT,
            start_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
            instant_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
            status="pending",
        )

    # DATE_TARGET with datetime instead of date
    with pytest.raises(ValueError):
        AgendaItem(
            item_id="cmt:1",
            source_domain="commitments",
            source_entity_id="1",
            display_title="Bad Date Target",
            temporal_kind=AgendaTemporalKind.DATE_TARGET,
            target_date=datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc),  # datetime not allowed
            status="active",
        )


# ==============================================================================
# Invariant I: Event Overlap Factual Classification
# ==============================================================================

@pytest.mark.asyncio
async def test_i_event_overlap_factual_classification(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="ev_a",
            title="Meeting A",
            start_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc),
            user_id=user_id,
        ),
        CalendarEvent(
            event_id="ev_b",
            title="Meeting B",
            start_at=datetime(2026, 9, 28, 10, 30, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 11, 30, tzinfo=timezone.utc),
            user_id=user_id,
        ),
    ], user_id=user_id)

    agenda = await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    assert len(agenda.conflicts) == 1
    conflict = agenda.conflicts[0]
    assert conflict.conflict_type == ConflictType.EVENT_OVERLAP
    assert conflict.severity == ConflictSeverity.INFO
    assert "overlap by 30 minutes" in conflict.description


# ==============================================================================
# Invariant J: Deadline During Event
# ==============================================================================

@pytest.mark.asyncio
async def test_j_deadline_during_event(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="ev_client",
            title="Client Presentation",
            start_at=datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
            user_id=user_id,
        )
    ], user_id=user_id)

    task = await env["task_mgr"].create_task(
        title="Submit RFP",
        user_id=user_id,
        due_at=datetime(2026, 9, 28, 13, 30, tzinfo=timezone.utc),
    )

    agenda = await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    deadline_conflicts = [c for c in agenda.conflicts if c.conflict_type == ConflictType.DEADLINE_DURING_EVENT]
    assert len(deadline_conflicts) == 1
    c = deadline_conflicts[0]
    assert c.severity == ConflictSeverity.WARNING
    assert "Submit RFP" in c.description


# ==============================================================================
# Invariant K: Reminder During Event
# ==============================================================================

@pytest.mark.asyncio
async def test_k_reminder_during_event(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="ev_sync",
            title="Team Sync",
            start_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc),
            user_id=user_id,
        )
    ], user_id=user_id)

    await env["rem_mgr"].create_reminder(
        title="Ping accountant",
        user_id=user_id,
        scheduled_at=datetime(2026, 9, 28, 15, 15, tzinfo=timezone.utc),
    )

    agenda = await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    rem_conflicts = [c for c in agenda.conflicts if c.conflict_type == ConflictType.REMINDER_DURING_EVENT]
    assert len(rem_conflicts) == 1
    assert rem_conflicts[0].severity == ConflictSeverity.INFO


# ==============================================================================
# Invariant L: Commitment / Event Informational Relationship
# ==============================================================================

@pytest.mark.asyncio
async def test_l_commitment_event_informational(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="ev_all_day",
            title="Offsite",
            start_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc),
            user_id=user_id,
        )
    ], user_id=user_id)

    await env["cmt_mgr"].create_commitment(
        title="Quarterly Review",
        user_id=user_id,
        target_date=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
    )

    agenda = await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    # Commitment target sharing a date with an event is NOT a conflict
    cmt_event_conflicts = [c for c in agenda.conflicts if "commitments" in str(c.item_ids) and "calendar" in str(c.item_ids)]
    assert len(cmt_event_conflicts) == 0


# ==============================================================================
# Invariant M: Task / Commitment Inconsistency
# ==============================================================================

@pytest.mark.asyncio
async def test_m_task_commitment_inconsistency(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    cmt = await env["cmt_mgr"].create_commitment(
        title="Deliver MVP",
        user_id=user_id,
        target_date=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc),
    )

    await env["task_mgr"].create_task(
        title="Write Documentation",
        user_id=user_id,
        due_at=datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc),
        source_commitment_id=cmt.id,
    )

    agenda = await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    inconsistencies = [c for c in agenda.conflicts if c.conflict_type == ConflictType.TASK_DEADLINE_EXCEEDS_COMMITMENT_TARGET]
    assert len(inconsistencies) == 1
    assert inconsistencies[0].severity == ConflictSeverity.WARNING


# ==============================================================================
# Invariant N: Linked Commitment Target Before Task Deadline Advisory
# ==============================================================================

@pytest.mark.asyncio
async def test_n_linked_commitment_target_precedes_task_deadline(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # Setup calendar snapshot
    await env["cal_mgr"].set_events([], user_id=user_id)

    cmt = await env["cmt_mgr"].create_commitment(
        title="Parent Goal",
        user_id=user_id,
        target_date=datetime(2026, 9, 25, 0, 0, tzinfo=timezone.utc),
    )

    task = await env["task_mgr"].create_task(
        title="Subtask With Later Deadline",
        user_id=user_id,
        due_at=datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc),
        source_commitment_id=cmt.id,
    )

    proposal = await service.propose_schedule_slot(
        user_id=user_id,
        task_id=task.id,
        duration_minutes=60,
        date_str="2026-09-28",
    )
    assert proposal.feasible is True
    assert "LINKED_COMMITMENT_TARGET_PRECEDES_TASK_DEADLINE" in proposal.warnings


# ==============================================================================
# Invariant O: Free Slot Calculation & Exact Boundary Rules
# ==============================================================================

@pytest.mark.asyncio
async def test_o_free_slot_temporal_feasibility(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # Event from 10:00 to 11:30 UTC
    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="ev_midday",
            title="Midday Meeting",
            start_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 11, 30, tzinfo=timezone.utc),
            user_id=user_id,
        )
    ], user_id=user_id)

    # Working hours 09:00 to 18:00 UTC
    agenda = await service.get_daily_agenda(
        user_id=user_id,
        target_date=date(2026, 9, 28),
        explicit_tz="UTC",
        work_start_hour=9,
        work_end_hour=18,
    )

    # Should have two gaps: 09:00 - 10:00 (60m) and 11:30 - 18:00 (390m)
    assert len(agenda.free_slots) == 2
    gap1 = agenda.free_slots[0]
    assert gap1.start_at == datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
    assert gap1.end_at == datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
    assert gap1.duration_minutes == 60

    gap2 = agenda.free_slots[1]
    assert gap2.start_at == datetime(2026, 9, 28, 11, 30, tzinfo=timezone.utc)
    assert gap2.end_at == datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
    assert gap2.duration_minutes == 390


# ==============================================================================
# Invariant P & Q: Missing Duration and No Feasible Slot
# ==============================================================================

@pytest.mark.asyncio
async def test_p_q_missing_duration_and_no_feasible_slot(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cal_mgr"].set_events([
        # Meeting taking almost entire day 09:00 to 17:50
        CalendarEvent(
            event_id="ev_all_day_block",
            title="All Day Strategy",
            start_at=datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 17, 50, tzinfo=timezone.utc),
            user_id=user_id,
        )
    ], user_id=user_id)

    task = await env["task_mgr"].create_task(title="Quick Task", user_id=user_id)

    # Missing duration / < 15 min rejected
    with pytest.raises(ValueError):
        await service.propose_schedule_slot(user_id=user_id, task_id=task.id, duration_minutes=10)

    # Requesting 30 min when only 10 min available -> NO_FEASIBLE_SLOT
    proposal = await service.propose_schedule_slot(
        user_id=user_id,
        task_id=task.id,
        duration_minutes=30,
        date_str="2026-09-28",
        explicit_tz="UTC",
    )
    assert proposal.feasible is False
    assert proposal.reason == "NO_FEASIBLE_SLOT"


# ==============================================================================
# Invariant R: All-Day Event Behavior
# ==============================================================================

@pytest.mark.asyncio
async def test_r_all_day_event_behavior(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="ev_holiday",
            title="Public Holiday",
            all_day=True,
            start_at=datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc),
            end_at=datetime(2026, 9, 28, 23, 59, 59, tzinfo=timezone.utc),
            user_id=user_id,
        )
    ], user_id=user_id)

    agenda = await service.get_daily_agenda(
        user_id=user_id,
        target_date=date(2026, 9, 28),
        explicit_tz="UTC",
        work_start_hour=9,
        work_end_hour=18,
    )
    # All-day event must NOT subtract hourly slots
    assert len(agenda.free_slots) == 1
    assert agenda.free_slots[0].duration_minutes == 540  # Full 9 hours free


# ==============================================================================
# Invariant S & T: Missing and Empty Calendar Snapshot
# ==============================================================================

@pytest.mark.asyncio
async def test_s_t_missing_and_empty_calendar_snapshot(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    other_user = "usr_no_snapshot"
    await env["perm_mgr"].update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=other_user)
    await env["perm_mgr"].update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=other_user)

    # No snapshot reported yet
    agenda = await service.get_daily_agenda(user_id=other_user, target_date=date(2026, 9, 28))
    cal_omitted = [s for s in agenda.omitted_sources if s["domain"] == "calendar"]
    assert len(cal_omitted) == 1
    assert cal_omitted[0]["reason"] == "NO_SNAPSHOT_REPORTED"


# ==============================================================================
# Invariant U & V: Partial Permissions and Zero Permissions Fail-Closed
# ==============================================================================

@pytest.mark.asyncio
async def test_u_v_partial_and_zero_permissions(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_partial = "usr_partial"

    # Grant only Tasks permission
    await env["perm_mgr"].update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=user_partial)

    agenda = await service.get_daily_agenda(user_id=user_partial, target_date=date(2026, 9, 28))
    omitted = {s["domain"] for s in agenda.omitted_sources}
    assert "calendar" in omitted
    assert "commitments" in omitted
    assert "reminders" in omitted

    # Zero permissions fails closed
    user_zero = "usr_zero_perms"
    with pytest.raises(PermissionError) as exc_info:
        await service.get_daily_agenda(user_id=user_zero, target_date=date(2026, 9, 28))
    assert "No personal agenda domains are authorized" in str(exc_info.value)


# ==============================================================================
# Invariant W: Cross-User Isolation
# ==============================================================================

@pytest.mark.asyncio
async def test_w_cross_user_isolation(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_a = "usr_alice"
    user_b = "usr_bob"

    for u in (user_a, user_b):
        await env["perm_mgr"].update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=u)

    await env["task_mgr"].create_task(title="Alice Private Task", user_id=user_a, due_at=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc))
    await env["task_mgr"].create_task(title="Bob Private Task", user_id=user_b, due_at=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc))

    agenda_a = await service.get_daily_agenda(user_id=user_a, target_date=date(2026, 9, 28))
    titles_a = [it.display_title for it in agenda_a.items]
    assert "Alice Private Task" in titles_a
    assert "Bob Private Task" not in titles_a


# ==============================================================================
# Invariant X: Deterministic Ordering
# ==============================================================================

@pytest.mark.asyncio
async def test_x_deterministic_ordering(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cmt_mgr"].create_commitment(
        title="A Goal",
        priority=CommitmentPriority.LOW,
        user_id=user_id,
        target_date=datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc),
    )
    await env["cmt_mgr"].create_commitment(
        title="B Goal",
        priority=CommitmentPriority.HIGH,
        user_id=user_id,
        target_date=datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc),
    )
    await env["task_mgr"].create_task(
        title="Afternoon Task",
        user_id=user_id,
        due_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
    )

    agenda = await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    # High priority commitment appears before low priority commitment
    assert agenda.items[0].display_title == "B Goal"
    assert agenda.items[1].display_title == "A Goal"
    # Timed task appears in chronological section
    assert agenda.items[2].display_title == "Afternoon Task"


# ==============================================================================
# Invariants Y, Z, AA: Tools (get_daily_agenda, propose_schedule_slot, detect_schedule_conflicts)
# ==============================================================================

@pytest.mark.asyncio
async def test_y_z_aa_agenda_tools(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # 1. GetDailyAgendaTool
    tool_agenda = GetDailyAgendaTool(agenda_service=service)
    res_agenda = await tool_agenda.execute({"_user_id": user_id, "date": "2026-09-28"})
    assert res_agenda["status"] == "success"
    assert "agenda" in res_agenda

    # 2. ProposeScheduleSlotTool
    await env["cal_mgr"].set_events([], user_id=user_id)
    task = await env["task_mgr"].create_task(title="Slot Task", user_id=user_id)
    tool_slot = ProposeScheduleSlotTool(agenda_service=service)
    res_slot = await tool_slot.execute({
        "_user_id": user_id,
        "task_id": task.id,
        "duration_minutes": 45,
        "date": "2026-09-28",
    })
    assert res_slot["status"] == "success"
    assert res_slot["proposal"]["feasible"] is True

    # 3. DetectScheduleConflictsTool
    tool_conflicts = DetectScheduleConflictsTool(agenda_service=service)
    res_conf = await tool_conflicts.execute({
        "_user_id": user_id,
        "window_hours": 48,
    })
    assert res_conf["status"] == "success"
    assert "conflicts" in res_conf


# ==============================================================================
# Invariants AB & AC: Proactive Conflict Allowlist Gate
# ==============================================================================

@pytest.mark.asyncio
async def test_ab_ac_proactive_allowlist_gate(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    now_utc = datetime.now(timezone.utc)
    # Two overlapping meetings (EVENT_OVERLAP) and a task deadline collision (DEADLINE_DURING_EVENT)
    await env["cal_mgr"].set_events([
        CalendarEvent(
            event_id="ev_meet1",
            title="Meeting 1",
            start_at=now_utc + timedelta(hours=1),
            end_at=now_utc + timedelta(hours=2),
            user_id=user_id,
        ),
        CalendarEvent(
            event_id="ev_meet2",
            title="Meeting 2",
            start_at=now_utc + timedelta(hours=1, minutes=30),
            end_at=now_utc + timedelta(hours=2, minutes=30),
            user_id=user_id,
        ),
    ], user_id=user_id)

    await env["task_mgr"].create_task(
        title="Meeting Deadline Task",
        user_id=user_id,
        due_at=now_utc + timedelta(hours=1, minutes=15),
    )

    evaluator = ScheduleConflictEvaluator(agenda_service=service)
    candidates = await evaluator.evaluate_proposals(user_id=user_id)

    # DEADLINE_DURING_EVENT must generate a proactive candidate
    deadline_candidates = [c for c in candidates if "Deadline" in c.title]
    assert len(deadline_candidates) == 1

    # EVENT_OVERLAP must NOT generate a proactive candidate by default
    overlap_candidates = [c for c in candidates if c.metadata.get("conflict_type") == ConflictType.EVENT_OVERLAP.value]
    assert len(overlap_candidates) == 0


# ==============================================================================
# Invariant AD: Context Budget Compliance
# ==============================================================================

@pytest.mark.asyncio
async def test_ad_context_budget_compliance(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    provider = AgendaContextProvider(agenda_service=service, max_chars=1000)

    # Irrelevant query returns None
    block_none = await provider.provide_context(query="calculate fibonacci of 10", user_id=user_id)
    assert block_none is None

    # Relevant query returns formatted block within bounds
    block = await provider.provide_context(query="what is my agenda today?", user_id=user_id)
    assert block is not None
    assert len(block.content) <= 1000
    assert "[Today's Agenda" in block.content


# ==============================================================================
# Invariant AE: Invalid Working Hours Range
# ==============================================================================

@pytest.mark.asyncio
async def test_ae_invalid_working_hours_range(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    # work_start_hour >= work_end_hour
    with pytest.raises(ValueError) as exc:
        await service.get_daily_agenda(
            user_id=user_id,
            work_start_hour=17,
            work_end_hour=9,
        )
    assert "strictly less than" in str(exc.value)


# ==============================================================================
# Invariant AF: REST Endpoint Contracts
# ==============================================================================

@pytest_asyncio.fixture
async def phase6d_api(tmp_path):
    credentials = SQLiteCredentialStore(str(tmp_path / "phase6d-auth.db"))
    secret = await credentials.issue_provisioning()
    provisioned = await provision_credential(secret, credentials)
    resolver = IdentityResolver(credentials)

    perm_store = InMemoryPermissionStore()
    perm_mgr = PermissionManager(perm_store)
    for p in (PermissionType.CALENDAR, PermissionType.TASKS, PermissionType.COMMITMENTS, PermissionType.REMINDERS):
        await perm_mgr.update_permission(p, PermissionStatus.AUTHORIZED, user_id=provisioned.user.user_id)

    cal_mgr = CalendarManager(permission_manager=perm_mgr)
    task_mgr = TaskManager(store=InMemoryTaskStore())
    cmt_mgr = CommitmentManager(store=InMemoryCommitmentStore())
    rem_mgr = ReminderManager(store=InMemoryReminderStore())

    service = AgendaService(
        calendar_manager=cal_mgr,
        task_manager=task_mgr,
        commitment_manager=cmt_mgr,
        reminder_manager=rem_mgr,
        permission_manager=perm_mgr,
    )

    app.dependency_overrides[get_credential_store_dependency] = lambda: credentials
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: resolver
    app.dependency_overrides[get_permission_manager_dependency] = lambda: perm_mgr
    app.dependency_overrides[get_agenda_service_dependency] = lambda: service

    yield {
        "token": provisioned.credential,
        "user_id": provisioned.user.user_id,
        "service": service,
        "cal_mgr": cal_mgr,
        "task_mgr": task_mgr,
    }

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_af_rest_endpoints(phase6d_api):
    token = phase6d_api["token"]
    user_id = phase6d_api["user_id"]
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/agenda/daily
        resp = await client.get("/api/v1/agenda/daily?date=2026-09-28", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["target_date"] == "2026-09-28"

        # Invalid timezone -> 422
        resp_bad_tz = await client.get("/api/v1/agenda/daily?timezone=Bad/Tz", headers=headers)
        assert resp_bad_tz.status_code == 422

        # Invalid working hours -> 422
        resp_bad_hours = await client.get("/api/v1/agenda/daily?work_start_hour=18&work_end_hour=9", headers=headers)
        assert resp_bad_hours.status_code == 422

        # 2. GET /api/v1/agenda/conflicts
        resp_conf = await client.get("/api/v1/agenda/conflicts?window_hours=24", headers=headers)
        assert resp_conf.status_code == 200
        assert "conflicts" in resp_conf.json()

        # 3. POST /api/v1/agenda/propose-slot
        await phase6d_api["cal_mgr"].set_events([], user_id=user_id)
        task = await phase6d_api["task_mgr"].create_task(title="API Slot Task", user_id=user_id)
        resp_slot = await client.post(
            "/api/v1/agenda/propose-slot",
            json={
                "task_id": task.id,
                "duration_minutes": 30,
                "date": "2026-09-28",
            },
            headers=headers,
        )
        assert resp_slot.status_code == 200
        assert resp_slot.json()["feasible"] is True


# ==============================================================================
# Invariants AG & AH: Zero Mutation and Zero Client Actions
# ==============================================================================

@pytest.mark.asyncio
async def test_ag_ah_zero_mutation_and_zero_client_actions(phase6d_environment):
    env = phase6d_environment
    service: AgendaService = env["service"]
    user_id = env["user_id"]

    await env["cal_mgr"].set_events([], user_id=user_id)
    task = await env["task_mgr"].create_task(title="Invariant Task", user_id=user_id)

    # Initial state
    t_before = await env["task_mgr"].get_task(task.id, user_id=user_id)

    # Execute agenda operations
    await service.get_daily_agenda(user_id=user_id, target_date=date(2026, 9, 28))
    await service.propose_schedule_slot(user_id=user_id, task_id=task.id, duration_minutes=30)
    await service.detect_conflicts(user_id=user_id, window_hours=24)

    # Verify zero mutations on task
    t_after = await env["task_mgr"].get_task(task.id, user_id=user_id)
    assert t_after.updated_at == t_before.updated_at
    assert t_after.due_at == t_before.due_at
    assert t_after.status == t_before.status
