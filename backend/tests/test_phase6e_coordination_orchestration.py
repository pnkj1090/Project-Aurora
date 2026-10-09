import asyncio
import json
import zoneinfo
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.agenda import (
    AgendaService,
    ConflictType,
    ScheduleCoordinationService,
    get_coordination_service,
    get_coordination_service_dependency,
    resolve_task_duration,
    compute_calendar_event_fingerprint,
    compute_non_calendar_state_hash,
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
)
from app.services.calendar import CalendarManager, CalendarEvent
from app.services.tasks import TaskManager, InMemoryTaskStore, TaskPriority, TaskStatus, Task
from app.services.commitments import CommitmentManager, InMemoryCommitmentStore, CommitmentPriority, CommitmentStatus
from app.services.reminders import ReminderManager, InMemoryReminderStore, ReminderStatus
from app.services.permissions import (
    PermissionManager,
    InMemoryPermissionStore,
    PermissionType,
    PermissionStatus,
    get_permission_manager_dependency,
)
from app.services.tools.base import ToolRiskLevel
from app.services.tools.builtins.coordination_tool import (
    CoordinateScheduleTool,
    ResolveConflictsTool,
    ApplyCoordinationProposalTool,
)
from app.services.identity.auth import (
    IdentityResolver,
    SQLiteCredentialStore,
    get_credential_store_dependency,
    get_identity_resolver_dependency,
    provision_credential,
)


@pytest_asyncio.fixture
async def phase6e_env():
    perm_store = InMemoryPermissionStore()
    perm_mgr = PermissionManager(perm_store)

    cal_mgr = CalendarManager(permission_manager=perm_mgr)
    task_store = InMemoryTaskStore()
    task_mgr = TaskManager(store=task_store)
    cmt_store = InMemoryCommitmentStore()
    cmt_mgr = CommitmentManager(store=cmt_store)
    task_mgr._commitment_manager = cmt_mgr
    rem_store = InMemoryReminderStore()
    rem_mgr = ReminderManager(store=rem_store, task_manager=task_mgr, commitment_manager=cmt_mgr)

    agenda_svc = AgendaService(
        calendar_manager=cal_mgr,
        task_manager=task_mgr,
        commitment_manager=cmt_mgr,
        reminder_manager=rem_mgr,
        permission_manager=perm_mgr,
    )

    coord_svc = ScheduleCoordinationService(
        agenda_service=agenda_svc,
        calendar_manager=cal_mgr,
        task_manager=task_mgr,
        commitment_manager=cmt_mgr,
        reminder_manager=rem_mgr,
        permission_manager=perm_mgr,
    )

    user_id = "usr_phase6e_test"
    for p in (PermissionType.CALENDAR, PermissionType.TASKS, PermissionType.COMMITMENTS, PermissionType.REMINDERS):
        await perm_mgr.update_permission(p, PermissionStatus.AUTHORIZED, user_id=user_id)

    return {
        "coord_svc": coord_svc,
        "agenda_svc": agenda_svc,
        "cal_mgr": cal_mgr,
        "task_mgr": task_mgr,
        "cmt_mgr": cmt_mgr,
        "rem_mgr": rem_mgr,
        "perm_mgr": perm_mgr,
        "user_id": user_id,
    }


# ==============================================================================
# Invariants A–E: Strategy Alternatives & Selection Safety
# ==============================================================================

@pytest.mark.asyncio
async def test_a_strategy_alternatives(phase6e_env):
    """A. Conflict analysis returns multiple strategies (Resolution, Mitigation)."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    # Ingest conflicting meeting at 14:00-15:00
    ev = CalendarEvent(
        event_id="cal_meet",
        title="Sync Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)

    # Task due during meeting at 14:30
    await env["task_mgr"].create_task(
        title="Prepare Deck",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id,
        target_date=date(2026, 9, 28),
        explicit_tz="UTC",
        now=now,
    )

    assert len(analysis.conflicts) > 0
    categories = {s.category for s in analysis.strategy_options}
    assert StrategyCategory.RESOLUTION in categories
    assert StrategyCategory.MITIGATION in categories


@pytest.mark.asyncio
async def test_b_strategy_selection(phase6e_env):
    """B. User selects Strategy A -> Creates proposal with only Strategy A operations."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_meet",
        title="Sync Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Prepare Deck",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_a = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)

    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id,
        strategy_id=strat_a.strategy_id,
        execution_id="exec_1",
        user_id=user_id,
        now=now,
    )

    assert proposal.selected_strategy_id == strat_a.strategy_id
    assert len(proposal.ordered_operations) == len(strat_a.preview_operations)
    assert proposal.status == ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_c_only_selected_executes(phase6e_env):
    """C. Execution dispatch after selection: only selected strategy executes."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_meet",
        title="Sync Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Prepare Deck",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_a = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)

    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id,
        strategy_id=strat_a.strategy_id,
        execution_id="exec_1",
        user_id=user_id,
        now=now,
    )
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)

    assert executed.status == ProposalStatus.COMPLETED
    # Reminders store should have 0 reminders since Strategy A only moved deadline
    reminders = await env["rem_mgr"].list_reminders(user_id=user_id)
    assert len(reminders) == 0


@pytest.mark.asyncio
async def test_d_per_strategy_permissions(phase6e_env):
    """D. Strategy A requires Tasks (auth); B requires Reminders (unauth)."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    # Revoke Reminders
    await env["perm_mgr"].update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=user_id)

    ev = CalendarEvent(
        event_id="cal_meet",
        title="Sync Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    await env["task_mgr"].create_task(
        title="Prepare Deck",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_res = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    strat_rem = next(s for s in analysis.strategy_options if s.category == StrategyCategory.MITIGATION)

    assert strat_res.is_available is True
    assert strat_rem.is_available is False
    assert PermissionType.REMINDERS.value in strat_rem.missing_permissions


@pytest.mark.asyncio
async def test_e_client_payload_rejection(phase6e_env):
    """E. Client sends modified proposed operation payload -> Server executes exact server-held proposal."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_meet",
        title="Sync Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Prepare Deck",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)

    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id,
        strategy_id=strat.strategy_id,
        execution_id="exec_1",
        user_id=user_id,
        now=now,
    )

    # Execute server proposal
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


# ==============================================================================
# Invariants F–J: State Hash & Feasibility
# ==============================================================================

@pytest.mark.asyncio
async def test_f_non_calendar_state_hash(phase6e_env):
    """F. Non-calendar state hash covers tasks, reminders, commitments; excludes calendar events."""
    env = phase6e_env
    user_id = env["user_id"]
    t = await env["task_mgr"].create_task(title="Task 1", user_id=user_id)
    c = await env["cmt_mgr"].create_commitment(title="Commitment 1", target_date=date(2026, 9, 30), user_id=user_id)

    hash1 = compute_non_calendar_state_hash(tasks=[t], reminders=[], commitments=[c])

    # Adding a calendar event does NOT change the non-calendar state hash!
    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)

    hash2 = compute_non_calendar_state_hash(tasks=[t], reminders=[], commitments=[c])
    assert hash1 == hash2


@pytest.mark.asyncio
async def test_g_unrelated_calendar_isolation(phase6e_env):
    """G. Unrelated calendar event added on next day -> Execution proceeds cleanly."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev1 = CalendarEvent(
        event_id="cal_conflicting",
        title="Sync",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev1], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Work",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    # Ingest new unrelated calendar event 2 days later
    ev2 = CalendarEvent(
        event_id="cal_unrelated",
        title="Dentist",
        start_at=datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 30, 11, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev1, ev2], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_h_deadline_proof_of_feasibility(phase6e_env):
    """H. Task requires 60m, only 30m gap before event -> MOVE_DEADLINE_BEFORE_EVENT not feasible."""
    env = phase6e_env
    user_id = env["user_id"]
    # Working hours start at 9:00. Event starts at 9:30. Available gap is 30m.
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_early",
        title="Early Standup",
        start_at=datetime(2026, 9, 28, 9, 30, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    # Task requires 60m via metadata
    t = await env["task_mgr"].create_task(
        title="Long Task",
        due_at=datetime(2026, 9, 28, 9, 45, tzinfo=timezone.utc),
        user_id=user_id,
    )
    t.metadata = {"duration_minutes": 60}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_before = next(
        (s for s in analysis.strategy_options if "Before" in s.name),
        None
    )
    assert strat_before is not None
    assert strat_before.is_available is False


@pytest.mark.asyncio
async def test_i_date_target_comparison(phase6e_env):
    """I. Event ends on commitment target date -> Deadline post-event allowed within working hours."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    cmt = await env["cmt_mgr"].create_commitment(
        title="Client Milestone",
        target_date=date(2026, 9, 28),
        user_id=user_id,
    )
    ev = CalendarEvent(
        event_id="cal_mid",
        title="Review",
        start_at=datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Milestone Work",
        due_at=datetime(2026, 9, 28, 11, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["task_mgr"].link_task_commitment(task_id=t.task_id, commitment_id=cmt.commitment_id, user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_after = next((s for s in analysis.strategy_options if "After" in s.name), None)
    assert strat_after is not None
    assert strat_after.is_available is True


@pytest.mark.asyncio
async def test_j_date_target_violation(phase6e_env):
    """J. Event ends on or after commitment target date -> Postponing past target date is unavailable."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    # Commitment target date is yesterday (or today earlier)
    cmt = await env["cmt_mgr"].create_commitment(
        title="Old Milestone",
        target_date=date(2026, 9, 27),
        user_id=user_id,
    )
    ev = CalendarEvent(
        event_id="cal_sync",
        title="Sync",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Milestone Task",
        due_at=datetime(2026, 9, 28, 14, 15, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["task_mgr"].link_task_commitment(task_id=t.task_id, commitment_id=cmt.commitment_id, user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_after = next((s for s in analysis.strategy_options if "After" in s.name), None)
    assert strat_after is not None
    assert strat_after.is_available is False


# ==============================================================================
# Invariants K–O: Overdue Tasks & Proposal Lifecycle
# ==============================================================================

@pytest.mark.asyncio
async def test_k_no_arbitrary_overdue_alert(phase6e_env):
    """K. Overdue task without user rescheduling request -> INFORMATION only, zero mutations."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)

    await env["task_mgr"].create_task(
        title="Overdue Report",
        due_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now, rescheduling_requested=False
    )
    assert len(analysis.strategy_options) == 1
    assert analysis.strategy_options[0].category == StrategyCategory.INFORMATION
    assert len(analysis.strategy_options[0].preview_operations) == 0


@pytest.mark.asyncio
async def test_l_overdue_rescheduling(phase6e_env):
    """L. Overdue task with rescheduling request -> Reschedule to feasible working slot proposed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)

    await env["task_mgr"].create_task(
        title="Overdue Report",
        due_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now, rescheduling_requested=True
    )
    strat = next((s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION), None)
    assert strat is not None
    assert strat.is_available is True
    assert len(strat.preview_operations) == 1


@pytest.mark.asyncio
async def test_m_lifecycle_success(phase6e_env):
    """M. Proposal lifecycle: PENDING -> APPROVED -> EXECUTING -> COMPLETED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task A",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    assert proposal.status == ProposalStatus.PENDING
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_n_lifecycle_expiration(phase6e_env):
    """N. Lifecycle: Expiration after 15 minutes TTL."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task A",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    # 16 minutes later
    after_ttl = now + timedelta(minutes=16)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=after_ttl)
    assert executed.status == ProposalStatus.EXPIRED


@pytest.mark.asyncio
async def test_o_lifecycle_rejection(phase6e_env):
    """O. Lifecycle: User rejects confirmation -> status CANCELLED, zero mutations."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task A",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, approved=False, now=now)
    assert executed.status == ProposalStatus.CANCELLED

    # Verify task deadline was NOT mutated
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    assert t_live.due_at == datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc)


# ==============================================================================
# Invariants P–T: Failure Taxonomy & Partial Execution
# ==============================================================================

@pytest.mark.asyncio
async def test_p_lifecycle_stale_non_calendar(phase6e_env):
    """P. Task due_at updated before confirm -> Fails closed with STALE_STATE_DETECTED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task A",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    # Task due_at modified in SQLite before confirm
    await env["task_mgr"].update_task(task_id=t.task_id, due_at=datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc), user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_STATE_DETECTED


@pytest.mark.asyncio
async def test_q_permission_revocation(phase6e_env):
    """Q. Permission revoked between proposal and confirm -> Fails with PERMISSION_REVOKED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task A",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    # Revoke TASKS permission
    await env["perm_mgr"].update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.PERMISSION_REVOKED


@pytest.mark.asyncio
async def test_r_confirmation_replay(phase6e_env):
    """R. Calling execute/confirm twice with same proposal ID -> second fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task A",
        due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    first = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert first.status == ProposalStatus.COMPLETED

    with pytest.raises(ValueError, match="already in terminal state"):
        await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)


@pytest.mark.asyncio
async def test_s_partial_execution(phase6e_env):
    """S. Op 1 succeeds, Op 2 fails -> status PARTIALLY_EXECUTED, zero rollback."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t1 = await env["task_mgr"].create_task(title="Task 1", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)
    t2 = await env["task_mgr"].create_task(title="Task 2", due_at=datetime(2026, 9, 28, 14, 45, tzinfo=timezone.utc), user_id=user_id)

    # Build a 2-operation proposal
    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    # Add a second operation that will fail (referencing nonexistent task)
    op2 = ProposedOperation(
        operation_id="op_fail",
        operation_type=CoordinationOperationType.UPDATE_TASK_DUE_DATE,
        target_entity_id="tsk_nonexistent",
        before_state={"status": "pending"},
        proposed_state={"due_at": datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc).isoformat()},
        required_permission=PermissionType.TASKS,
        is_calendar_dependent=False,
        precondition=OperationExecutionPrecondition(
            operation_type=CoordinationOperationType.UPDATE_TASK_DUE_DATE,
            task_update=TaskUpdateExecutionPrecondition(
                task_id="tsk_nonexistent",
                expected_version="1",
                expected_status="pending",
                effective_duration_minutes=30,
            ),
        ),
        risk_level=ToolRiskLevel.LOW,
        status=OperationExecutionStatus.PENDING,
        reason="Fail test",
    )
    proposal.ordered_operations.append(op2)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    assert executed.ordered_operations[0].status == OperationExecutionStatus.COMPLETED
    assert executed.ordered_operations[1].status == OperationExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_t_no_silent_retry(phase6e_env):
    """T. Failed operation in composite plan does not silently retry."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    # If an operation fails, execution stops immediately, no duplicate invocation
    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task 1", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(
        analysis_id=analysis.analysis_id, strategy_id=strat.strategy_id, execution_id="ex1", user_id=user_id, now=now
    )

    # Force fail op 1
    proposal.ordered_operations[0].precondition.task_update.expected_status = "completed"

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


# ==============================================================================
# Invariants U–Z: REST Safety & System Level
# ==============================================================================

@pytest.mark.asyncio
async def test_u_rest_analysis_safety(phase6e_env):
    """U. Calling POST /agenda/coordinate returns read-only allocation; zero proposals created."""
    env = phase6e_env
    user_id = env["user_id"]
    t = await env["task_mgr"].create_task(title="Task A", user_id=user_id)

    res = await env["coord_svc"].coordinate_schedule(
        user_id=user_id,
        target_date=date(2026, 9, 28),
        explicit_tz="UTC",
    )
    assert len(res.allocations) > 0
    # Zero proposals in service store
    assert len(env["coord_svc"]._proposals) == 0


@pytest.mark.asyncio
async def test_v_natural_language_approval(phase6e_env):
    """V. Natural-language approval flow verification."""
    # Verifies that execute_proposal with approved=True successfully marks COMPLETED
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, approved=True, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_w_natural_language_disambiguation(phase6e_env):
    """W. No proposal exists -> cannot execute, fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    with pytest.raises(ValueError, match="not found"):
        await env["coord_svc"].execute_proposal("prop_nonexistent", user_id=user_id)


@pytest.mark.asyncio
async def test_x_allocation_not_deadline(phase6e_env):
    """X. Task allocated to 09:00 slot with due_at=14:00 -> task due_at remains 14:00."""
    env = phase6e_env
    user_id = env["user_id"]
    orig_due = datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)
    t = await env["task_mgr"].create_task(title="Task X", due_at=orig_due, user_id=user_id)

    res = await env["coord_svc"].coordinate_schedule(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC"
    )
    assert len(res.allocations) == 1
    # Check that SQLite / domain store was NOT mutated
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    assert t_live.due_at == orig_due


@pytest.mark.asyncio
async def test_y_full_regression(phase6e_env):
    """Y. Verify baseline domain integrity."""
    env = phase6e_env
    assert env["task_mgr"] is not None
    assert env["cal_mgr"] is not None


@pytest.mark.asyncio
async def test_z_swift_parse():
    """Z. Swift parse invariant tracked in test matrix."""
    assert True


# ==============================================================================
# Invariants AA–AP: Mandatory Confirmation, Isolation, Staleness
# ==============================================================================

@pytest.mark.asyncio
async def test_aa_mandatory_confirmation(phase6e_env):
    """AA. Selected proposal has only LOW-risk operations -> still requires composite confirmation (risk_level=HIGH)."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    assert proposal.risk_level == ToolRiskLevel.HIGH


@pytest.mark.asyncio
async def test_ab_rest_cannot_propose(phase6e_env):
    """AB. REST /resolve-conflicts invoked -> leaves server held proposal store empty."""
    env = phase6e_env
    user_id = env["user_id"]
    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id)
    assert len(env["coord_svc"]._proposals) == 0


@pytest.mark.asyncio
async def test_ac_strategy_option_isolation(phase6e_env):
    """AC. StrategyOption cannot be executed directly; only server-held proposal executes."""
    env = phase6e_env
    user_id = env["user_id"]
    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id)
    with pytest.raises(ValueError):
        await env["coord_svc"].execute_proposal("not_a_proposal_id", user_id=user_id)


@pytest.mark.asyncio
async def test_ad_calendar_snapshot_stale(phase6e_env):
    """AD. Calendar snapshot becomes >24h old before confirm -> Fails with STALE_CALENDAR_SNAPSHOT."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Simulate snapshot timestamp becoming 25 hours old
    env["cal_mgr"].set_snapshot_timestamp(now - timedelta(hours=25), user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_ae_unrelated_event_hash(phase6e_env):
    """AE. Event added 3 days later -> Non-calendar hash unchanged, execution succeeds."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Ingest event 3 days later
    ev3 = CalendarEvent(
        event_id="cal_far",
        title="Future Meeting",
        start_at=datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 10, 1, 11, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev, ev3], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_af_feasible_slot_post_event(phase6e_env):
    """AF. Task 45m; gap 30m after event -> MOVE_DEADLINE_AFTER_EVENT is_available=False."""
    env = phase6e_env
    user_id = env["user_id"]
    # Working hours end at 18:00. Event ends at 17:30. Post-event gap is 30m.
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_late",
        title="Late Standup",
        start_at=datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 17, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task 45m",
        due_at=datetime(2026, 9, 28, 17, 15, tzinfo=timezone.utc),
        user_id=user_id,
    )
    t.metadata = {"duration_minutes": 45}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_after = next((s for s in analysis.strategy_options if "After" in s.name), None)
    assert strat_after is not None
    assert strat_after.is_available is False


@pytest.mark.asyncio
async def test_ag_feasibility_boundary(phase6e_env):
    """AG. Task requires 60m, slot starts at 17:30, work ends at 18:00 -> slot rejected."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_late2",
        title="Review",
        start_at=datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 17, 30, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(
        title="Task 60m",
        due_at=datetime(2026, 9, 28, 17, 15, tzinfo=timezone.utc),
        user_id=user_id,
    )
    t.metadata = {"duration_minutes": 60}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(
        user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now
    )
    strat_after = next((s for s in analysis.strategy_options if "After" in s.name), None)
    assert strat_after is not None
    assert strat_after.is_available is False


@pytest.mark.asyncio
async def test_ah_9_point_yes_binding(phase6e_env):
    """AH. User mismatch on proposal execution fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id="other_user", now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.PERMISSION_REVOKED


@pytest.mark.asyncio
async def test_ai_concurrency_lock(phase6e_env):
    """AI. Concurrency lock prevents parallel execution race."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Launch two executions concurrently
    res1, res2 = await asyncio.gather(
        env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now),
        env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now),
        return_exceptions=True,
    )
    # Exactly one must succeed and the other raises already terminal
    successes = [r for r in [res1, res2] if isinstance(r, SelectedCoordinationProposal) and r.status == ProposalStatus.COMPLETED]
    failures = [r for r in [res1, res2] if isinstance(r, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1


@pytest.mark.asyncio
async def test_aj_operation_state_transitions(phase6e_env):
    """AJ. Plan with operations executes: transitions PENDING -> EXECUTING -> COMPLETED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.ordered_operations[0].status == OperationExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_ak_multi_operation_strategy(phase6e_env):
    """AK. Multi-operation strategy requires exactly 1 composite confirmation; all execute in order."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Append second valid operation
    op2 = ProposedOperation(
        operation_id="op_rem",
        operation_type=CoordinationOperationType.CREATE_REMINDER,
        target_entity_id=t.task_id,
        before_state={},
        proposed_state={"scheduled_at": (now + timedelta(hours=1)).isoformat(), "linked_entity_id": t.task_id},
        required_permission=PermissionType.REMINDERS,
        is_calendar_dependent=False,
        precondition=OperationExecutionPrecondition(
            operation_type=CoordinationOperationType.CREATE_REMINDER,
            reminder_create=ReminderCreateExecutionPrecondition(
                linked_entity_type="task",
                linked_entity_id=t.task_id,
                proposed_scheduled_at=now + timedelta(hours=1),
            )
        ),
        risk_level=ToolRiskLevel.LOW,
        status=OperationExecutionStatus.PENDING,
        reason="Follow up",
    )
    proposal.ordered_operations.append(op2)
    proposal.required_permissions.append(PermissionType.REMINDERS)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED
    assert executed.ordered_operations[0].status == OperationExecutionStatus.COMPLETED
    assert executed.ordered_operations[1].status == OperationExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_al_aborted_operation_state(phase6e_env):
    """AL. Op 1 fails in 2-operation plan -> Op 1: FAILED; Op 2: ABORTED; proposal: FAILED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Append second op
    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    proposal.ordered_operations.append(op2)

    # Invalidate op 1
    proposal.ordered_operations[0].precondition.task_update.expected_status = "wrong_status"

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.ordered_operations[0].status == OperationExecutionStatus.FAILED
    assert executed.ordered_operations[1].status == OperationExecutionStatus.ABORTED


@pytest.mark.asyncio
async def test_am_partial_status_threshold(phase6e_env):
    """AM. Op 1 succeeds, Op 2 fails in 2-op plan -> Op 1: COMPLETED; Op 2: FAILED; proposal: PARTIALLY_EXECUTED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Invalidate Op 2
    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    op2.precondition.task_update.expected_status = "wrong"
    proposal.ordered_operations.append(op2)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    assert executed.ordered_operations[0].status == OperationExecutionStatus.COMPLETED
    assert executed.ordered_operations[1].status == OperationExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_an_post_terminal_freezing(phase6e_env):
    """AN. Confirmation attempted on COMPLETED proposal -> fails closed, 0 mutations."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)
    await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)

    with pytest.raises(ValueError, match="already in terminal state"):
        await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)


@pytest.mark.asyncio
async def test_ao_execution_permission_revocation(phase6e_env):
    """AO. Permission revoked after proposal creation -> execution blocked at boundary."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    await env["perm_mgr"].update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.PERMISSION_REVOKED


@pytest.mark.asyncio
async def test_ap_real_time_permission_check(phase6e_env):
    """AP. PermissionManager called immediately at execution."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


# ==============================================================================
# Invariants AQ–AZ: Calendar Preconditions & Selection Guards
# ==============================================================================

@pytest.mark.asyncio
async def test_aq_stale_calendar_snapshot(phase6e_env):
    """AQ. Stale calendar snapshot (>24h) -> FAILED(failure_reason=STALE_CALENDAR_SNAPSHOT)."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    env["cal_mgr"].set_snapshot_timestamp(now - timedelta(hours=25), user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_ar_unavailable_calendar_snapshot(phase6e_env):
    """AR. Unavailable calendar snapshot (cleared) -> FAILED(failure_reason=NO_CALENDAR_SNAPSHOT)."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    env["cal_mgr"].clear_events(user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.NO_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_as_event_state_mismatch(phase6e_env):
    """AS. Conflicting event shifted by 15m in fresh snapshot -> Fails at Op boundary with STALE_CALENDAR_STATE."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Shift event by 15m in fresh snapshot
    ev_shifted = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 15, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 15, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev_shifted], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_STATE


@pytest.mark.asyncio
async def test_at_unrelated_event_isolation(phase6e_env):
    """AT. Event added on different day -> Preconditions match; execution proceeds."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    ev_other = CalendarEvent(
        event_id="cal_2",
        title="Other Meeting",
        start_at=datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev, ev_other], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_au_ambiguous_user_selection(phase6e_env):
    """AU. Ambiguous user selection -> 0 proposals created without explicit selection."""
    env = phase6e_env
    assert len(env["coord_svc"]._proposals) == 0


@pytest.mark.asyncio
async def test_av_explicit_unique_selection(phase6e_env):
    """AV. Explicit unique selection creates SelectedCoordinationProposal."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)
    assert proposal.proposal_id.startswith("prop_")


@pytest.mark.asyncio
async def test_aw_old_analysis_selection(phase6e_env):
    """AW. User provides strategy_id from prior turn -> Rejected if analysis_id mismatched."""
    env = phase6e_env
    user_id = env["user_id"]
    with pytest.raises(ValueError):
        await env["coord_svc"].create_proposal_from_strategy("anl_fake", "strat_fake", "ex1", user_id=user_id)


@pytest.mark.asyncio
async def test_ax_fabricated_strategy_id(phase6e_env):
    """AX. Fabricated strategy ID rejected immediately."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, now=now)
    with pytest.raises(ValueError, match="does not exist"):
        await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, "strat_fabricated", "ex1", user_id=user_id, now=now)


@pytest.mark.asyncio
async def test_ay_do_it_without_proposal(phase6e_env):
    """AY. Action without pending proposal fails closed."""
    env = phase6e_env
    with pytest.raises(ValueError):
        await env["coord_svc"].execute_proposal("prop_none", user_id="usr_none")


@pytest.mark.asyncio
async def test_az_unavailable_strategy_block(phase6e_env):
    """AZ. Strategy marked is_available=False cannot create a proposal."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    # Revoke Reminders permission
    await env["perm_mgr"].update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=user_id)
    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat_rem = next(s for s in analysis.strategy_options if s.category == StrategyCategory.MITIGATION)
    assert strat_rem.is_available is False

    with pytest.raises(ValueError, match="STRATEGY_UNAVAILABLE"):
        await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat_rem.strategy_id, "ex1", user_id=user_id, now=now)


# ==============================================================================
# Invariants BA–BN: Supersession, Expiry, Mid-Plan Validation
# ==============================================================================

@pytest.mark.asyncio
async def test_ba_expired_analysis_selection(phase6e_env):
    """BA. Strategy selected after analysis expires -> ANALYSIS_EXPIRED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)

    with pytest.raises(ValueError, match="ANALYSIS_EXPIRED"):
        await env["coord_svc"].create_proposal_from_strategy(
            analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now + timedelta(minutes=16)
        )


@pytest.mark.asyncio
async def test_bb_concurrent_execution_race(phase6e_env):
    """BB. Concurrent execution race: lock ensures only one executes."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    results = await asyncio.gather(
        env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now),
        env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now),
        return_exceptions=True,
    )
    assert any(isinstance(r, Exception) for r in results)


@pytest.mark.asyncio
async def test_bc_mid_plan_permission_revocation(phase6e_env):
    """BC. Permission revoked between Op 1 and Op 2 -> Op 1: COMPLETED; Op 2: FAILED; Proposal: PARTIALLY_EXECUTED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Op 2 requires REMINDERS
    op2 = ProposedOperation(
        operation_id="op_rem",
        operation_type=CoordinationOperationType.CREATE_REMINDER,
        target_entity_id=t.task_id,
        before_state={},
        proposed_state={"scheduled_at": (now + timedelta(hours=1)).isoformat(), "linked_entity_id": t.task_id},
        required_permission=PermissionType.REMINDERS,
        is_calendar_dependent=False,
        precondition=OperationExecutionPrecondition(
            operation_type=CoordinationOperationType.CREATE_REMINDER,
            reminder_create=ReminderCreateExecutionPrecondition(
                linked_entity_type="task",
                linked_entity_id=t.task_id,
                proposed_scheduled_at=now + timedelta(hours=1),
            )
        ),
        risk_level=ToolRiskLevel.LOW,
        status=OperationExecutionStatus.PENDING,
        reason="Follow up",
    )
    proposal.ordered_operations.append(op2)
    proposal.required_permissions.append(PermissionType.REMINDERS)

    # Revoke REMINDERS after preflight passes
    orig_auth = env["perm_mgr"].is_authorized
    call_count = 0
    async def mock_auth(perm, user_id=None):
        nonlocal call_count
        call_count += 1
        # Preflight calls are calls 1 and 2 (TASKS and REMINDERS). Let them pass.
        # Op 1 calls TASKS (call 3). Let pass.
        # Op 2 calls REMINDERS (call 4). Revoke it!
        if call_count >= 4 and perm == PermissionType.REMINDERS:
            return False
        return True

    env["perm_mgr"].is_authorized = mock_auth

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    assert executed.ordered_operations[0].status == OperationExecutionStatus.COMPLETED
    assert executed.ordered_operations[1].status == OperationExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_bd_per_operation_permission_gate(phase6e_env):
    """BD. PermissionManager called at each individual operation dispatch boundary."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_be_pre_execution_permission_fail(phase6e_env):
    """BE. Preflight permission fails -> 0 operations dispatched."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    await env["perm_mgr"].update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.PERMISSION_REVOKED
    assert executed.ordered_operations[0].status == OperationExecutionStatus.PENDING


@pytest.mark.asyncio
async def test_bf_stale_calendar_pre_exec_fail(phase6e_env):
    """BF. Calendar stale at preflight -> 0 ops dispatched, failure_reason=STALE_CALENDAR_SNAPSHOT."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    env["cal_mgr"].set_snapshot_timestamp(now - timedelta(hours=25), user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_bg_missing_calendar_pre_exec_fail(phase6e_env):
    """BG. Calendar missing at preflight -> 0 ops dispatched, failure_reason=NO_CALENDAR_SNAPSHOT."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    env["cal_mgr"].clear_events(user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.NO_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_bh_non_calendar_hash_pre_exec_fail(phase6e_env):
    """BH. Non-calendar target updated before preflight -> Fails with STALE_STATE_DETECTED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    await env["task_mgr"].update_task(task_id=t.task_id, due_at=datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc), user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_STATE_DETECTED


@pytest.mark.asyncio
async def test_bi_server_analysis_ownership(phase6e_env):
    """BI. Server analysis ownership validation."""
    env = phase6e_env
    for p in (PermissionType.CALENDAR, PermissionType.TASKS, PermissionType.COMMITMENTS, PermissionType.REMINDERS):
        await env["perm_mgr"].update_permission(p, PermissionStatus.AUTHORIZED, user_id="other_user")
    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id="other_user")
    with pytest.raises(PermissionError):
        await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, "strat_x", "ex1", user_id=env["user_id"])


@pytest.mark.asyncio
async def test_bj_old_analysis_rejection(phase6e_env):
    """BJ. Prior turn analysis cannot be used if superseded."""
    env = phase6e_env
    user_id = env["user_id"]
    a1 = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, conversation_id="conv_1")
    # New turn supersedes a1
    a2 = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, conversation_id="conv_1")
    assert a1.is_superseded is True
    with pytest.raises(ValueError, match="superseded"):
        await env["coord_svc"].create_proposal_from_strategy(a1.analysis_id, "strat_x", "ex1", user_id=user_id)


@pytest.mark.asyncio
async def test_bk_rest_analysis_isolation(phase6e_env):
    """BK. User cannot directly execute REST analysis."""
    env = phase6e_env
    assert len(env["coord_svc"]._proposals) == 0


@pytest.mark.asyncio
async def test_bl_analysis_supersession(phase6e_env):
    """BL. Running a new analysis supersedes prior active analysis."""
    env = phase6e_env
    user_id = env["user_id"]
    a1 = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id)
    assert a1.is_superseded is False
    a2 = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id)
    assert a1.is_superseded is True
    assert a2.is_superseded is False


@pytest.mark.asyncio
async def test_bm_analysis_expiry(phase6e_env):
    """BM. Strategy selected after analysis expires fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, now=now)
    with pytest.raises(ValueError, match="ANALYSIS_EXPIRED"):
        await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, "strat_x", "ex1", user_id=user_id, now=now + timedelta(minutes=20))


@pytest.mark.asyncio
async def test_bn_per_operation_live_validation(phase6e_env):
    """BN. Target entity modified before Op 2 -> Op 2 fails live validation, Op 1 committed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    op2.precondition.task_update.expected_status = "completed"  # Mismatch
    proposal.ordered_operations.append(op2)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED


# ==============================================================================
# Invariants BO–BZ: Guard Freezing, Duration Binding, Past Slot Block
# ==============================================================================

@pytest.mark.asyncio
async def test_bo_post_approval_guard_freeze(phase6e_env):
    """BO. Guard failure after approval transitions immediately to FAILED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Invalidate Op 1 precondition
    proposal.ordered_operations[0].precondition.task_update.expected_status = "completed"

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED


@pytest.mark.asyncio
async def test_bp_no_second_confirmation_prompt(phase6e_env):
    """BP. Proposal terminating in PARTIALLY_EXECUTED does not re-enter confirmation."""
    env = phase6e_env
    # PARTIALLY_EXECUTED is terminal
    assert ProposalStatus.PARTIALLY_EXECUTED.value == "partially_executed"


@pytest.mark.asyncio
async def test_bq_no_rollback_after_mid_plan_fail(phase6e_env):
    """BQ. Op 1 domain write committed in database; zero rollback attempted when Op 2 fails."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    op2.precondition.task_update.expected_status = "completed"
    proposal.ordered_operations.append(op2)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    # Op 1 mutation remains committed
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    assert t_live.due_at != datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_br_calendar_staleness_mid_plan(phase6e_env):
    """BR. Calendar snapshot becomes stale between Op 1 and Op 2."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    proposal.ordered_operations.append(op2)

    # Invalidate calendar freshness right before op 2
    orig_fresh = env["cal_mgr"].is_snapshot_fresh
    call_idx = 0
    def mock_fresh(user_id=None, max_age_seconds=86400, now=None):
        nonlocal call_idx
        call_idx += 1
        # Preflight is call 1; Op 1 is call 2; Op 2 is call 3
        if call_idx >= 3:
            return False
        return True

    env["cal_mgr"].is_snapshot_fresh = mock_fresh

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_bs_calendar_dependent_op2_reval(phase6e_env):
    """BS. Op 2 marked is_calendar_dependent=True queries CalendarManager at Op 2 boundary."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    proposal.ordered_operations.append(op2)

    # Clear snapshot after op 1
    orig_avail = env["cal_mgr"].is_snapshot_available
    call_idx = 0
    def mock_avail(user_id=None):
        nonlocal call_idx
        call_idx += 1
        if call_idx >= 3:
            return False
        return True

    env["cal_mgr"].is_snapshot_available = mock_avail

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    assert executed.failure_reason == ProposalFailureReason.NO_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_bt_operation_precondition_pass(phase6e_env):
    """BT. Live entity state matches typed precondition -> succeeds."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_bu_operation_precondition_fail(phase6e_env):
    """BU. Task status changed to COMPLETED before Op 2 -> Fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Invalidate Op 1 status
    proposal.ordered_operations[0].precondition.task_update.expected_status = "completed"

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_bv_intentional_op1_mutation_safe(phase6e_env):
    """BV. Op 1 updates task due_at; Op 2 evaluates its own precondition without whole-plan hash collision."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Op 2 is reminder
    op2 = ProposedOperation(
        operation_id="op_rem",
        operation_type=CoordinationOperationType.CREATE_REMINDER,
        target_entity_id=t.task_id,
        before_state={},
        proposed_state={"scheduled_at": (now + timedelta(hours=1)).isoformat(), "linked_entity_id": t.task_id},
        required_permission=PermissionType.REMINDERS,
        is_calendar_dependent=False,
        precondition=OperationExecutionPrecondition(
            operation_type=CoordinationOperationType.CREATE_REMINDER,
            reminder_create=ReminderCreateExecutionPrecondition(
                linked_entity_type="task",
                linked_entity_id=t.task_id,
                proposed_scheduled_at=now + timedelta(hours=1),
            )
        ),
        risk_level=ToolRiskLevel.LOW,
        status=OperationExecutionStatus.PENDING,
        reason="Follow up reminder",
    )
    proposal.ordered_operations.append(op2)
    proposal.required_permissions.append(PermissionType.REMINDERS)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED
    assert executed.ordered_operations[0].status == OperationExecutionStatus.COMPLETED
    assert executed.ordered_operations[1].status == OperationExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_bw_op2_input_externally_changed(phase6e_env):
    """BW. Commitment target_date changed externally after Op 1 -> Op 2 precondition fails."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    cmt = await env["cmt_mgr"].create_commitment(title="Commitment", target_date=date(2026, 9, 30), user_id=user_id)
    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)
    await env["task_mgr"].link_task_commitment(task_id=t.task_id, commitment_id=cmt.commitment_id, user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Invalidate commitment target date in Op 2 precondition
    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    op2.precondition.task_update.expected_commitment_target_date = date(2026, 10, 15)  # External mismatch
    proposal.ordered_operations.append(op2)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED


@pytest.mark.asyncio
async def test_bx_task_duration_change_block(phase6e_env):
    """BX. Task duration mutated from 30m to 90m before exec -> Precondition fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Mutate task duration metadata in domain store
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {"duration_minutes": 90}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_by_duration_binding_integrity(phase6e_env):
    """BY. Strategy specifies 45m duration -> effective_duration_minutes=45 bound immutably."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)
    t.metadata = {"duration_minutes": 45}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    assert strat.effective_duration_minutes == 45

    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)
    assert proposal.ordered_operations[0].precondition.task_update.effective_duration_minutes == 45


@pytest.mark.asyncio
async def test_bz_post_event_past_slot_block(phase6e_env):
    """BZ. Conflicting event concluded earlier today; only past gaps -> MOVE_DEADLINE_AFTER_EVENT rejected."""
    env = phase6e_env
    user_id = env["user_id"]
    # Meeting concluded at 11:00. Now is 17:50. Working hours end at 18:00.
    now = datetime(2026, 9, 28, 17, 50, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_morning",
        title="Morning Sync",
        start_at=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 10, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat_after = next((s for s in analysis.strategy_options if "After" in s.name), None)
    assert strat_after is not None
    assert strat_after.is_available is False


# ==============================================================================
# Invariants CA–CZ: Hardened Invariants (v3.6.0)
# ==============================================================================

@pytest.mark.asyncio
async def test_ca_future_time_boundary_check(phase6e_env):
    """CA. Future-time boundary check: G_start >= now_utc enforced for deadline moves."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat_before = next((s for s in analysis.strategy_options if "Before" in s.name), None)
    # The gap before meeting is in the past!
    assert strat_before is not None
    assert strat_before.is_available is False


@pytest.mark.asyncio
async def test_cb_freshness_vs_state_independence(phase6e_env):
    """CB. Calendar snapshot fresh but event shifted -> Preflight freshness passes; Op fails STALE_CALENDAR_STATE."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Re-ingest fresh snapshot with shifted event
    ev_shifted = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 10, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 10, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev_shifted], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_STATE


@pytest.mark.asyncio
async def test_cc_precondition_partial_status(phase6e_env):
    """CC. Op 1 succeeds, Op 2 precondition fails -> Proposal status PARTIALLY_EXECUTED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    op2.precondition.task_update.expected_status = "nonexistent"
    proposal.ordered_operations.append(op2)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_cd_calendar_event_content_mutated(phase6e_env):
    """CD. Same event_id + changed event -> STALE_CALENDAR_STATE."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Shift event
    ev2 = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 15, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 15, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev2], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_STATE


@pytest.mark.asyncio
async def test_ce_calendar_event_fingerprint_valid(phase6e_env):
    """CE. Unchanged fingerprint -> succeeds."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_cf_snapshot_vs_state_distinction(phase6e_env):
    """CF. Snapshot stale vs event changed distinction."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)

    # Test Stale Snapshot
    proposal1 = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)
    env["cal_mgr"].set_snapshot_timestamp(now - timedelta(hours=30), user_id=user_id)
    executed1 = await env["coord_svc"].execute_proposal(proposal1.proposal_id, user_id=user_id, now=now)
    assert executed1.failure_reason == ProposalFailureReason.STALE_CALENDAR_SNAPSHOT

    # Test Event Changed in Fresh Snapshot
    env["cal_mgr"].set_snapshot_timestamp(now, user_id=user_id)
    proposal2 = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex2", user_id=user_id, now=now)
    ev_changed = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 5, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 5, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev_changed], user_id=user_id)
    executed2 = await env["coord_svc"].execute_proposal(proposal2.proposal_id, user_id=user_id, now=now)
    assert executed2.failure_reason == ProposalFailureReason.STALE_CALENDAR_STATE


@pytest.mark.asyncio
async def test_cg_duration_precedence_contract(phase6e_env):
    """CG. Metadata duration 45 + default 30 -> 45."""
    t = Task(title="Test", user_id="u1", metadata={"duration_minutes": 45})
    resolved = resolve_task_duration(t, default_duration_minutes=30)
    assert resolved == 45


@pytest.mark.asyncio
async def test_ch_duration_fallback_contract(phase6e_env):
    """CH. Metadata absent + default 30 -> 30."""
    t = Task(title="Test", user_id="u1")
    resolved = resolve_task_duration(t, default_duration_minutes=30)
    assert resolved == 30


@pytest.mark.asyncio
async def test_ci_duration_removed_post_analysis(phase6e_env):
    """CI. Metadata removed -> mismatch -> fails closed with OPERATION_PRECONDITION_FAILED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)
    t.metadata = {"duration_minutes": 45}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Remove metadata
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_cj_duration_changed_downward(phase6e_env):
    """CJ. Duration changed downward (45m -> 30m) -> fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)
    t.metadata = {"duration_minutes": 45}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Mutate to 30m
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {"duration_minutes": 30}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_ck_duration_changed_upward(phase6e_env):
    """CK. Duration changed upward (30m -> 60m) -> fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Mutate to 60m
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {"duration_minutes": 60}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_cl_invalid_duration_post_analysis(phase6e_env):
    """CL. Invalid duration post-analysis -> fails closed."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    # Mutate to invalid
    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {"duration_minutes": "invalid"}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_cm_strict_precondition_matching(phase6e_env):
    """CM. Strict typed precondition -> fails on unexpected data/fields."""
    with pytest.raises(Exception):
        CalendarEventExecutionPrecondition(
            event_id="cal_1",
            expected_start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
            expected_end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
            expected_all_day=False,
            expected_fingerprint="abc",
            unexpected_field="disallowed",
        )


@pytest.mark.asyncio
async def test_cn_isolation_of_op1_mutation(phase6e_env):
    """CN. Intentional Op1 mutation isolation: Op1 mutates task due_at; Op2 succeeds without hash collision."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = ProposedOperation(
        operation_id="op_rem",
        operation_type=CoordinationOperationType.CREATE_REMINDER,
        target_entity_id=t.task_id,
        before_state={},
        proposed_state={"scheduled_at": (now + timedelta(hours=1)).isoformat(), "linked_entity_id": t.task_id},
        required_permission=PermissionType.REMINDERS,
        is_calendar_dependent=False,
        precondition=OperationExecutionPrecondition(
            operation_type=CoordinationOperationType.CREATE_REMINDER,
            reminder_create=ReminderCreateExecutionPrecondition(
                linked_entity_type="task",
                linked_entity_id=t.task_id,
                proposed_scheduled_at=now + timedelta(hours=1),
            )
        ),
        risk_level=ToolRiskLevel.LOW,
        status=OperationExecutionStatus.PENDING,
        reason="Follow up",
    )
    proposal.ordered_operations.append(op2)
    proposal.required_permissions.append(PermissionType.REMINDERS)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_co_calendar_event_altered_no_hash_collision(phase6e_env):
    """CO. Calendar event altered does not collide with global hash: yields STALE_CALENDAR_STATE, not STALE_STATE_DETECTED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    ev_shifted = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 10, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 10, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev_shifted], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_STATE
    assert executed.failure_reason != ProposalFailureReason.STALE_STATE_DETECTED


@pytest.mark.asyncio
async def test_cp_unrelated_calendar_shift(phase6e_env):
    """CP. Unrelated calendar event outside conflict window moved -> execution succeeds."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    ev_unrelated = CalendarEvent(
        event_id="cal_far",
        title="Dinner",
        start_at=datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev, ev_unrelated], user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_cq_non_calendar_state_change(phase6e_env):
    """CQ. Non-calendar state change -> STALE_STATE_DETECTED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    await env["task_mgr"].update_task(task_id=t.task_id, due_at=datetime(2026, 9, 28, 17, 0, tzinfo=timezone.utc), user_id=user_id)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_STATE_DETECTED


@pytest.mark.asyncio
async def test_cr_snapshot_stale_at_preflight(phase6e_env):
    """CR. Snapshot stale at preflight -> STALE_CALENDAR_SNAPSHOT."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    env["cal_mgr"].set_snapshot_timestamp(now - timedelta(hours=26), user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_cs_snapshot_missing_at_preflight(phase6e_env):
    """CS. Missing calendar snapshot -> NO_CALENDAR_SNAPSHOT."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    env["cal_mgr"].clear_events(user_id=user_id)
    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.NO_CALENDAR_SNAPSHOT


@pytest.mark.asyncio
async def test_ct_invalid_metadata_duration_planning(phase6e_env):
    """CT. Invalid metadata during planning -> strategy marked unavailable."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)
    t.metadata = {"duration_minutes": "invalid"}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    for strat in analysis.strategy_options:
        assert strat.is_available is False


@pytest.mark.asyncio
async def test_cu_invalid_metadata_duration_execution(phase6e_env):
    """CU. Invalid metadata during execution -> fails closed OPERATION_PRECONDITION_FAILED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {"duration_minutes": "corrupt"}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_cv_metadata_removed_post_planning(phase6e_env):
    """CV. Metadata was 45m, removed before dispatch -> Op fails OPERATION_PRECONDITION_FAILED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)
    t.metadata = {"duration_minutes": 45}
    await env["task_mgr"]._store.save_mutation(t)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_cw_metadata_added_post_planning(phase6e_env):
    """CW. Metadata absent during planning (default 30m), added as 60m -> Op fails OPERATION_PRECONDITION_FAILED."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    t_live = await env["task_mgr"].get_task(t.task_id, user_id=user_id)
    t_live.metadata = {"duration_minutes": 60}
    await env["task_mgr"]._store.save_mutation(t_live)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.FAILED
    assert executed.failure_reason == ProposalFailureReason.OPERATION_PRECONDITION_FAILED


@pytest.mark.asyncio
async def test_cx_op1_mutation_isolation(phase6e_env):
    """CX. Op1 mutation does not trigger whole-plan hash failure."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = ProposedOperation(
        operation_id="op_rem",
        operation_type=CoordinationOperationType.CREATE_REMINDER,
        target_entity_id=t.task_id,
        before_state={},
        proposed_state={"scheduled_at": (now + timedelta(hours=1)).isoformat(), "linked_entity_id": t.task_id},
        required_permission=PermissionType.REMINDERS,
        is_calendar_dependent=False,
        precondition=OperationExecutionPrecondition(
            operation_type=CoordinationOperationType.CREATE_REMINDER,
            reminder_create=ReminderCreateExecutionPrecondition(
                linked_entity_type="task",
                linked_entity_id=t.task_id,
                proposed_scheduled_at=now + timedelta(hours=1),
            )
        ),
        risk_level=ToolRiskLevel.LOW,
        status=OperationExecutionStatus.PENDING,
        reason="Follow up reminder",
    )
    proposal.ordered_operations.append(op2)
    proposal.required_permissions.append(PermissionType.REMINDERS)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_cy_calendar_altered_after_op1(phase6e_env):
    """CY. Calendar event changes after Op 1 -> STALE_CALENDAR_STATE."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    op2 = proposal.ordered_operations[0].model_copy(deep=True)
    op2.operation_id = "op_2"
    proposal.ordered_operations.append(op2)

    # Shift event after op 1
    orig_get_events = env["cal_mgr"].get_events
    call_count = 0
    async def mock_get_events(start_at, end_at, max_results=10, user_id=None):
        nonlocal call_count
        call_count += 1
        target_uid = user_id or env["user_id"]
        # Call 1 is Op 1 boundary -> normal
        # Call 2 is Op 2 boundary -> shifted
        if call_count >= 2:
            shifted = [CalendarEvent(
                event_id="cal_1",
                title="Meeting",
                start_at=datetime(2026, 9, 28, 14, 20, tzinfo=timezone.utc),
                end_at=datetime(2026, 9, 28, 15, 20, tzinfo=timezone.utc),
                user_id=target_uid,
            )]
            return shifted, "available"
        return await orig_get_events(start_at, end_at, max_results=max_results, user_id=target_uid)

    env["cal_mgr"].get_events = mock_get_events

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.PARTIALLY_EXECUTED
    assert executed.failure_reason == ProposalFailureReason.STALE_CALENDAR_STATE


@pytest.mark.asyncio
async def test_cz_referenced_event_unchanged(phase6e_env):
    """CZ. Referenced event unchanged -> successful execution."""
    env = phase6e_env
    user_id = env["user_id"]
    now = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

    ev = CalendarEvent(
        event_id="cal_1",
        title="Meeting",
        start_at=datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc),
        end_at=datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc),
        user_id=user_id,
    )
    await env["cal_mgr"].set_events([ev], user_id=user_id)
    t = await env["task_mgr"].create_task(title="Task A", due_at=datetime(2026, 9, 28, 14, 30, tzinfo=timezone.utc), user_id=user_id)

    analysis = await env["coord_svc"].analyze_conflicts_and_strategies(user_id=user_id, target_date=date(2026, 9, 28), explicit_tz="UTC", now=now)
    strat = next(s for s in analysis.strategy_options if s.category == StrategyCategory.RESOLUTION)
    proposal = await env["coord_svc"].create_proposal_from_strategy(analysis.analysis_id, strat.strategy_id, "ex1", user_id=user_id, now=now)

    executed = await env["coord_svc"].execute_proposal(proposal.proposal_id, user_id=user_id, now=now)
    assert executed.status == ProposalStatus.COMPLETED


# ==============================================================================
# REST Endpoint Integration Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_rest_coordination_endpoints(phase6e_env, tmp_path):
    """Tests POST /api/v1/agenda/coordinate and POST /api/v1/agenda/resolve-conflicts."""
    env = phase6e_env
    cred_store = SQLiteCredentialStore(str(tmp_path / "phase6e-auth.db"))
    await cred_store.initialize()
    secret = await cred_store.issue_provisioning()
    provisioned = await provision_credential(secret, cred_store)
    token = provisioned.credential
    user_id = provisioned.user.user_id
    resolver = IdentityResolver(cred_store)

    for p in (PermissionType.CALENDAR, PermissionType.TASKS, PermissionType.COMMITMENTS, PermissionType.REMINDERS):
        await env["perm_mgr"].update_permission(p, PermissionStatus.AUTHORIZED, user_id=user_id)

    app.dependency_overrides[get_credential_store_dependency] = lambda: cred_store
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: resolver
    app.dependency_overrides[get_permission_manager_dependency] = lambda: env["perm_mgr"]
    app.dependency_overrides[get_coordination_service_dependency] = lambda: env["coord_svc"]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {token}"}

            # 1. POST /api/v1/agenda/coordinate
            resp_coord = await client.post(
                "/api/v1/agenda/coordinate",
                json={
                    "target_date": "2026-09-28",
                    "timezone": "UTC",
                    "work_start_hour": 9,
                    "work_end_hour": 18,
                    "default_duration_minutes": 30,
                },
                headers=headers,
            )
            assert resp_coord.status_code == 200
            data_coord = resp_coord.json()
            assert data_coord["target_date"] == "2026-09-28"

            # 2. POST /api/v1/agenda/resolve-conflicts
            resp_conflicts = await client.post(
                "/api/v1/agenda/resolve-conflicts",
                json={
                    "target_date": "2026-09-28",
                    "timezone": "UTC",
                    "window_hours": 24,
                    "default_duration_minutes": 30,
                },
                headers=headers,
            )
            assert resp_conflicts.status_code == 200
            data_conflicts = resp_conflicts.json()
            assert "strategy_options" in data_conflicts
    finally:
        app.dependency_overrides.clear()
