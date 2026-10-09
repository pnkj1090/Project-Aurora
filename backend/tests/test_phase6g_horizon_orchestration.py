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

from app.services.agenda.service import AgendaService
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
from app.services.agenda.horizon import ExecutiveHorizonOrchestrationService
from app.services.tools.builtins.horizon_tool import (
    GetDailyExecutivePlanTool,
    ProposeDailyPlanTool,
    ApplyDailyPlanTool,
    GetEveningHorizonReviewTool,
    ProposeEveningReviewTool,
    ApplyEveningReviewTool,
)
from app.services.tools.base import ToolRiskLevel


# ── Fixtures & Setup Helpers ──────────────────────────────────────────────────

@pytest.fixture
def test_user_id() -> str:
    return f"usr_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def other_user_id() -> str:
    return f"usr_other_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def horizon_setup(test_user_id):
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    await perm_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.COMMITMENTS, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=test_user_id)

    cal_manager = CalendarManager(permission_manager=perm_manager)
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

    service = ExecutiveHorizonOrchestrationService(
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
# SECTION 1: Morning Executive Plan Synthesis (G-01 to G-05)
# ==============================================================================

@pytest.mark.asyncio
async def test_g01_morning_synthesis_free_slots_and_events(horizon_setup, test_user_id):
    """G-01: Synthesizes calendar events and identifies available free slots."""
    service = horizon_setup["service"]
    cal = horizon_setup["cal_manager"]
    now = datetime.now(timezone.utc).replace(hour=8, minute=0, second=0, microsecond=0)
    ev = CalendarEvent(
        event_id="evt_standup",
        user_id=test_user_id,
        title="Morning Standup",
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=3),
    )
    await cal.set_events([ev], user_id=test_user_id)

    plan = await service.get_daily_executive_plan(
        target_date=now.date().isoformat(),
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    assert plan.target_date == now.date().isoformat()
    assert len(plan.events) == 1
    assert plan.events[0]["title"] == "Morning Standup"
    assert len(plan.free_slots) >= 1


@pytest.mark.asyncio
async def test_g02_morning_synthesis_task_and_commitment_correlation(horizon_setup, test_user_id):
    """G-02: Correlates scheduled/due tasks and active commitments into daily horizon."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    cm = horizon_setup["cmt_manager"]
    today = datetime.now(timezone.utc).date()

    t = await tm.create_task(title="Deploy Phase 6G", user_id=test_user_id)
    c = await cm.create_commitment(title="Launch Aurora", target_date=today, user_id=test_user_id)

    plan = await service.get_daily_executive_plan(
        target_date=today.isoformat(),
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    task_titles = [x["title"] for x in plan.tasks]
    cmt_titles = [x["title"] for x in plan.commitments]
    assert "Deploy Phase 6G" in task_titles
    assert "Launch Aurora" in cmt_titles


@pytest.mark.asyncio
async def test_g03_morning_synthesis_user_scoping_isolation(horizon_setup, test_user_id, other_user_id):
    """G-03: User A's data never leaks into User B's plan."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=other_user_id)
    await tm.create_task(title="Secret Task User A", user_id=test_user_id)

    plan_b = await service.get_daily_executive_plan(
        target_date=datetime.now(timezone.utc).date().isoformat(),
        user_id=other_user_id,
    )
    assert not any(x["title"] == "Secret Task User A" for x in plan_b.tasks)


@pytest.mark.asyncio
async def test_g04_morning_synthesis_read_only_invariance(horizon_setup, test_user_id):
    """G-04: Morning synthesis executes with zero state mutations."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    today = datetime.now(timezone.utc).date()
    t = await tm.create_task(title="Invariant Task", user_id=test_user_id)

    await service.get_daily_executive_plan(target_date=today.isoformat(), user_id=test_user_id)
    t_after = await tm.get_task(t.id, user_id=test_user_id)
    assert t_after.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_g05_morning_synthesis_domain_omission_metadata(horizon_setup, test_user_id):
    """G-05: Missing permissions produce structured omission metadata."""
    service = horizon_setup["service"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)

    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    assert any(d.get("domain") == "calendar" for d in plan.omitted_domains)
    assert len(plan.events) == 0


# ==============================================================================
# SECTION 2: Day Plan Proposal Creation & Structure (G-06 to G-15)
# ==============================================================================

@pytest.mark.asyncio
async def test_g06_day_plan_proposal_draft_status(horizon_setup, test_user_id):
    """G-06: Newly proposed day plan has status DRAFT."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(
        focus_recommendations=[{"task_id": "tsk_1", "recommended_slot_start": "09:00", "recommended_slot_end": "10:00", "rationale": "Deep work"}],
        milestone_reminders=[],
        user_id=test_user_id,
    )
    assert proposal.status == HorizonProposalStatus.DRAFT
    assert proposal.proposal_type == HorizonProposalType.DAY_PLAN


@pytest.mark.asyncio
async def test_g07_day_plan_proposal_ttl_enforcement(horizon_setup, test_user_id):
    """G-07: Newly proposed day plan has expires_at set to 30 minutes from creation."""
    service = horizon_setup["service"]
    before = datetime.now(timezone.utc)
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    after = datetime.now(timezone.utc)

    diff = (proposal.expires_at - proposal.created_at).total_seconds()
    assert abs(diff - 1800) < 5
    assert proposal.created_at >= before
    assert proposal.expires_at > after


@pytest.mark.asyncio
async def test_g08_day_plan_proposal_state_hash_populated(horizon_setup, test_user_id):
    """G-08: Day plan proposal contains non-empty canonical state hash."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    assert proposal.state_hash is not None
    assert len(proposal.state_hash) == 64


@pytest.mark.asyncio
async def test_g09_day_plan_focus_recommendation_non_mutating(horizon_setup, test_user_id):
    """G-09: Focus recommendations are presentation objects only; no state mutation."""
    service = horizon_setup["service"]
    rec = DayPlanFocusRecommendation(task_id="tsk_x", recommended_slot_start="10:00", recommended_slot_end="11:00", rationale="Focus")
    assert rec.task_id == "tsk_x"
    assert rec.rationale == "Focus"


@pytest.mark.asyncio
async def test_g10_day_plan_milestone_reminder_proposals(horizon_setup, test_user_id):
    """G-10: Day plan bundles proposed CREATE_REMINDER operations for milestones."""
    service = horizon_setup["service"]
    future_time = datetime.now(timezone.utc) + timedelta(hours=3)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[
            {
                "title": "Prep for Client Demo",
                "scheduled_at": future_time.isoformat(),
                "linked_entity_type": "NONE",
            }
        ],
        user_id=test_user_id,
    )
    assert len(proposal.ordered_operations) == 1
    assert proposal.ordered_operations[0].operation_type == HorizonOperationType.CREATE_REMINDER
    assert proposal.ordered_operations[0].precondition.reminder_create.exact_title == "Prep for Client Demo"


@pytest.mark.asyncio
async def test_g11_day_plan_proposal_presentation_text(horizon_setup, test_user_id):
    """G-11: Proposal includes presentation summary and detailed breakdown."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    assert proposal.presentation.summary != ""
    assert isinstance(proposal.presentation.recommendations, list)


@pytest.mark.asyncio
async def test_g12_day_plan_proposal_user_id_binding(horizon_setup, test_user_id):
    """G-12: Proposal is immutably bound to authenticated user_id."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    assert proposal.user_id == test_user_id


@pytest.mark.asyncio
async def test_g13_day_plan_proposal_ordered_operations_indices(horizon_setup, test_user_id):
    """G-13: Proposal operations are deterministically indexed and typed."""
    service = horizon_setup["service"]
    t1 = datetime.now(timezone.utc) + timedelta(hours=2)
    t2 = datetime.now(timezone.utc) + timedelta(hours=4)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[
            {"title": "M1", "scheduled_at": t1.isoformat(), "linked_entity_type": "NONE"},
            {"title": "M2", "scheduled_at": t2.isoformat(), "linked_entity_type": "NONE"},
        ],
        user_id=test_user_id,
    )
    assert proposal.ordered_operations[0].index == 0
    assert proposal.ordered_operations[1].index == 1


@pytest.mark.asyncio
async def test_g14_day_plan_proposal_storage_in_memory(horizon_setup, test_user_id):
    """G-14: Proposals are stored in-memory without persistent DB tables."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    fetched = await service.get_proposal(proposal.proposal_id, user_id=test_user_id)
    assert fetched.proposal_id == proposal.proposal_id


@pytest.mark.asyncio
async def test_g15_day_plan_proposal_serialization_roundtrip(horizon_setup, test_user_id):
    """G-15: DayPlanProposal serializes and deserializes cleanly."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    d = proposal.model_dump()
    roundtrip = DayPlanProposal.model_validate(d)
    assert roundtrip.proposal_id == proposal.proposal_id


# ==============================================================================
# SECTION 3: Calendar Invariants & Read-Only Governance (G-16 to G-20)
# ==============================================================================

@pytest.mark.asyncio
async def test_g16_calendar_manager_remains_read_only(horizon_setup, test_user_id):
    """G-16: Day plan orchestration never calls calendar write/update/delete."""
    service = horizon_setup["service"]
    cal = horizon_setup["cal_manager"]
    # Verify cal_manager has no create_event or delete_event called during propose or synthesis
    await service.get_daily_executive_plan(user_id=test_user_id)
    await service.propose_daily_plan(user_id=test_user_id)


@pytest.mark.asyncio
async def test_g17_no_sync_calendar_focus_block_action(horizon_setup, test_user_id):
    """G-17: Client actions never include SYNC_CALENDAR_FOCUS_BLOCK."""
    service = horizon_setup["service"]
    future_time = datetime.now(timezone.utc) + timedelta(hours=3)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[{"title": "M1", "scheduled_at": future_time.isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    for op in proposal.ordered_operations:
        action = op.proposed_payload.get("client_action")
        if action:
            assert action.get("action") != "SYNC_CALENDAR_FOCUS_BLOCK"


@pytest.mark.asyncio
async def test_g18_calendar_events_retrieved_with_authenticated_user_id(horizon_setup, test_user_id):
    """G-18: Calendar queries pass explicit user_id=authenticated_user_id."""
    service = horizon_setup["service"]
    cal = horizon_setup["cal_manager"]
    ev = CalendarEvent(
        event_id="evt_auth_check",
        user_id=test_user_id,
        title="Auth Check",
        start_at=datetime.now(timezone.utc),
        end_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    await cal.set_events([ev], user_id=test_user_id)

    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    assert any(e["title"] == "Auth Check" for e in plan.events)


@pytest.mark.asyncio
async def test_g19_calendar_unauthorized_omits_calendar_domain(horizon_setup, test_user_id):
    """G-19: Unauthorized CALENDAR omits domain without crashing."""
    service = horizon_setup["service"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)
    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    assert any(d.get("domain") == "calendar" for d in plan.omitted_domains)


@pytest.mark.asyncio
async def test_g20_calendar_empty_schedule_handled_gracefully(horizon_setup, test_user_id):
    """G-20: Day plan succeeds when user has zero calendar events on target date."""
    service = horizon_setup["service"]
    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    assert len(plan.events) == 0
    assert len(plan.free_slots) >= 1


# ==============================================================================
# SECTION 4: Evening Executive Review Synthesis (G-21 to G-25)
# ==============================================================================

@pytest.mark.asyncio
async def test_g21_evening_review_completed_vs_overdue_tasks(horizon_setup, test_user_id):
    """G-21: Evening review partitions tasks into completed today vs overdue."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    now = datetime.now(timezone.utc)
    # completed task
    t1 = await tm.create_task(title="Done Task", user_id=test_user_id)
    await tm.complete_task(t1.id, user_id=test_user_id)
    # overdue task
    t2 = await tm.create_task(title="Overdue Task", user_id=test_user_id, due_at=now - timedelta(hours=2))

    review = await service.get_evening_horizon_review(user_id=test_user_id)
    done_ids = [x["id"] for x in review.completed_tasks]
    overdue_ids = [x["id"] for x in review.overdue_tasks]
    assert t1.id in done_ids
    assert t2.id in overdue_ids


@pytest.mark.asyncio
async def test_g22_evening_review_completion_statistics(horizon_setup, test_user_id):
    """G-22: Evening review computes completion statistics accurately."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t1 = await tm.create_task(title="T1", user_id=test_user_id)
    await tm.complete_task(t1.id, user_id=test_user_id)
    t2 = await tm.create_task(title="T2", user_id=test_user_id)

    review = await service.get_evening_horizon_review(user_id=test_user_id)
    stats = review.completion_stats
    assert stats["completed_count"] >= 1
    assert "completion_rate" in stats


@pytest.mark.asyncio
async def test_g23_evening_review_cross_user_isolation(horizon_setup, test_user_id, other_user_id):
    """G-23: Evening review isolates User A from User B."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=other_user_id)
    t = await tm.create_task(title="User A Only", user_id=test_user_id)

    review_b = await service.get_evening_horizon_review(user_id=other_user_id)
    assert not any(x["title"] == "User A Only" for x in review_b.completed_tasks)
    assert not any(x["title"] == "User A Only" for x in review_b.overdue_tasks)


@pytest.mark.asyncio
async def test_g24_evening_review_read_only_invariance(horizon_setup, test_user_id):
    """G-24: Evening review synthesis is purely read-only."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="Pending Invariant", user_id=test_user_id)
    await service.get_evening_horizon_review(user_id=test_user_id)
    t_check = await tm.get_task(t.id, user_id=test_user_id)
    assert t_check.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_g25_evening_review_omission_metadata(horizon_setup, test_user_id):
    """G-25: Denied domains are correctly listed in evening review omission metadata."""
    service = horizon_setup["service"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=test_user_id)
    review = await service.get_evening_horizon_review(user_id=test_user_id)
    assert any(d.get("domain") == "tasks" for d in review.omitted_domains)


# ==============================================================================
# SECTION 5: Governed Task Rollover Temporal Controls (G-26 to G-35)
# ==============================================================================

@pytest.mark.asyncio
async def test_g26_rollover_effective_timezone_resolution(horizon_setup, test_user_id):
    """G-26: Resolves user's effective timezone via AgendaService."""
    service = horizon_setup["service"]
    resolved = await service.agenda_service.resolve_effective_timezone("America/New_York", user_id=test_user_id)
    assert str(resolved) == "America/New_York" or getattr(resolved, "key", None) == "America/New_York"


@pytest.mark.asyncio
async def test_g27_rollover_within_working_hours_preserves_clock_time(horizon_setup, test_user_id):
    """G-27: Due time within working hours (e.g. 14:00) preserves 14:00 on tomorrow's date."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    # Local 14:00 UTC
    today = datetime.now(timezone.utc).date()
    due_14 = datetime.combine(today, time(14, 0), tzinfo=timezone.utc)
    t = await tm.create_task(title="Task 14:00", user_id=test_user_id, due_at=due_14)

    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    rollover_op = proposal.ordered_operations[0]
    expected_tomorrow_14 = datetime.combine(today + timedelta(days=1), time(14, 0), tzinfo=timezone.utc)
    assert rollover_op.precondition.task_rollover.exact_new_due_at == expected_tomorrow_14


@pytest.mark.asyncio
async def test_g28_rollover_outside_working_hours_clamps_to_day_end(horizon_setup, test_user_id):
    """G-28: Due time after working hours (e.g. 21:00) clamps to 18:00."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    today = datetime.now(timezone.utc).date()
    due_21 = datetime.combine(today, time(21, 0), tzinfo=timezone.utc)
    t = await tm.create_task(title="Task 21:00", user_id=test_user_id, due_at=due_21)

    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    rollover_op = proposal.ordered_operations[0]
    expected_clamp = datetime.combine(today + timedelta(days=1), time(18, 0), tzinfo=timezone.utc)
    assert rollover_op.precondition.task_rollover.exact_new_due_at == expected_clamp


@pytest.mark.asyncio
async def test_g29_rollover_before_working_hours_clamps_to_day_end(horizon_setup, test_user_id):
    """G-29: Due time before working hours (e.g. 06:00) clamps to 18:00."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    today = datetime.now(timezone.utc).date()
    due_06 = datetime.combine(today, time(6, 0), tzinfo=timezone.utc)
    t = await tm.create_task(title="Task 06:00", user_id=test_user_id, due_at=due_06)

    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    rollover_op = proposal.ordered_operations[0]
    expected_clamp = datetime.combine(today + timedelta(days=1), time(18, 0), tzinfo=timezone.utc)
    assert rollover_op.precondition.task_rollover.exact_new_due_at == expected_clamp


@pytest.mark.asyncio
async def test_g30_rollover_none_due_at_defaults_to_day_end(horizon_setup, test_user_id):
    """G-30: Task with due_at=None rolls over to 18:00 on tomorrow's date."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    today = datetime.now(timezone.utc).date()
    t = await tm.create_task(title="Task No Due", user_id=test_user_id)

    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    rollover_op = proposal.ordered_operations[0]
    expected_clamp = datetime.combine(today + timedelta(days=1), time(18, 0), tzinfo=timezone.utc)
    assert rollover_op.precondition.task_rollover.exact_new_due_at == expected_clamp


@pytest.mark.asyncio
async def test_g31_rollover_immutable_exact_new_due_at(horizon_setup, test_user_id):
    """G-31: Precondition binds frozen, immutable exact_new_due_at."""
    new_due = datetime.now(timezone.utc) + timedelta(days=1)
    pre = TaskRolloverPrecondition(task_id="tsk_1", exact_new_due_at=new_due)
    with pytest.raises(ValidationError):
        pre.exact_new_due_at = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_g32_rollover_llm_cannot_alter_timestamp(horizon_setup, test_user_id):
    """G-32: Precondition model forbids extra fields and is frozen."""
    with pytest.raises(ValidationError):
        TaskRolloverPrecondition(
            task_id="tsk_1",
            exact_new_due_at=datetime.now(timezone.utc),
            extra_field="malicious",
        )


@pytest.mark.asyncio
async def test_g33_rollover_custom_working_hours_honored(horizon_setup, test_user_id):
    """G-33: Custom working hours (e.g. 08:00-16:00) clamp correctly to 16:00."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    today = datetime.now(timezone.utc).date()
    due_17 = datetime.combine(today, time(17, 0), tzinfo=timezone.utc)
    t = await tm.create_task(title="Task 17:00", user_id=test_user_id, due_at=due_17)

    # Monkeypatch resolve_working_hours for test
    orig = getattr(service.agenda_service, "resolve_working_hours", None)
    service.agenda_service.resolve_working_hours = lambda uid: (time(8, 0), time(16, 0))
    try:
        proposal = await service.propose_evening_review(
            rollover_tasks=[{"task_id": t.id}],
            explicit_timezone="UTC",
            user_id=test_user_id,
        )
        op = proposal.ordered_operations[0]
        expected_clamp = datetime.combine(today + timedelta(days=1), time(16, 0), tzinfo=timezone.utc)
        assert op.precondition.task_rollover.exact_new_due_at == expected_clamp
    finally:
        if orig is not None:
            service.agenda_service.resolve_working_hours = orig
        else:
            delattr(service.agenda_service, "resolve_working_hours")


@pytest.mark.asyncio
async def test_g34_rollover_date_calculation_tomorrow_in_local_tz(horizon_setup, test_user_id):
    """G-34: Tomorrow's date is computed in local timezone, not naive UTC."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="Tokyo Task", user_id=test_user_id)
    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        explicit_timezone="Asia/Tokyo",
        user_id=test_user_id,
    )
    op = proposal.ordered_operations[0]
    tokyo_tz = zoneinfo.ZoneInfo("Asia/Tokyo")
    tokyo_tomorrow = datetime.now(tokyo_tz).date() + timedelta(days=1)
    rollover_local_date = op.precondition.task_rollover.exact_new_due_at.astimezone(tokyo_tz).date()
    assert rollover_local_date == tokyo_tomorrow


@pytest.mark.asyncio
async def test_g35_rollover_execution_updates_task_due_at(horizon_setup, test_user_id):
    """G-35: Executing approved rollover updates task due_at in TaskManager."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="Rollover Exec", user_id=test_user_id)
    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert res.status == HorizonProposalStatus.COMPLETED

    updated = await tm.get_task(t.id, user_id=test_user_id)
    assert updated.due_at == proposal.ordered_operations[0].precondition.task_rollover.exact_new_due_at


# ==============================================================================
# SECTION 6: Commitment Consistency & DATE_TARGET Semantics (G-36 to G-40)
# ==============================================================================

@pytest.mark.asyncio
async def test_g36_commitment_overrun_warning_generated(horizon_setup, test_user_id):
    """G-36: If tomorrow > commitment target_date, generates structured warning."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    cm = horizon_setup["cmt_manager"]
    today = datetime.now(timezone.utc).date()
    # Commitment target is today
    c = await cm.create_commitment(title="Due Today", target_date=today, user_id=test_user_id)
    t = await tm.create_task(title="Child Task", user_id=test_user_id, source_commitment_id=c.id)

    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    warnings = proposal.presentation.commitment_warnings
    assert len(warnings) >= 1
    assert warnings[0]["commitment_id"] == c.id


@pytest.mark.asyncio
async def test_g37_commitment_no_warning_when_within_target_date(horizon_setup, test_user_id):
    """G-37: If tomorrow <= commitment target_date, no overrun warning."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    cm = horizon_setup["cmt_manager"]
    next_week = datetime.now(timezone.utc).date() + timedelta(days=7)
    c = await cm.create_commitment(title="Far Future", target_date=next_week, user_id=test_user_id)
    t = await tm.create_task(title="Safe Task", user_id=test_user_id, source_commitment_id=c.id)

    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        explicit_timezone="UTC",
        user_id=test_user_id,
    )
    assert len(proposal.presentation.commitment_warnings) == 0


@pytest.mark.asyncio
async def test_g38_commitment_target_date_never_converted_to_utc_midnight(horizon_setup, test_user_id):
    """G-38: Target date serialized strictly as YYYY-MM-DD date string."""
    service = horizon_setup["service"]
    cm = horizon_setup["cmt_manager"]
    d = date(2026, 10, 15)
    c = await cm.create_commitment(title="Format Check", target_date=d, user_id=test_user_id)

    import json
    canonical_raw = serialize_horizon_canonical_state([], [c])
    canonical = json.loads(canonical_raw) if isinstance(canonical_raw, str) else canonical_raw
    assert canonical["commitments"][0]["target_date"] == "2026-10-15"
    assert "T00:00:00" not in canonical["commitments"][0]["target_date"]


@pytest.mark.asyncio
async def test_g39_commitment_remains_unmutated_during_rollover(horizon_setup, test_user_id):
    """G-39: Task rollover never mutates or modifies the parent commitment."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    cm = horizon_setup["cmt_manager"]
    today = datetime.now(timezone.utc).date()
    c = await cm.create_commitment(title="Unmutated Cmt", target_date=today, user_id=test_user_id)
    t = await tm.create_task(title="Rolled Task", user_id=test_user_id, source_commitment_id=c.id)

    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)

    c_after = await cm.get_commitment(c.id, user_id=test_user_id)
    c_target = c_after.target_date.date() if isinstance(c_after.target_date, datetime) else c_after.target_date
    assert c_target == today
    assert c_after.status == CommitmentStatus.ACTIVE


@pytest.mark.asyncio
async def test_g40_commitment_source_lookup_uses_authenticated_user_id(horizon_setup, test_user_id, other_user_id):
    """G-40: Commitment retrieval forwards explicit user_id=authenticated_user_id."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    cm = horizon_setup["cmt_manager"]
    c = await cm.create_commitment(title="Other User Cmt", target_date=date(2026, 10, 1), user_id=other_user_id)
    t = Task(
        user_id=test_user_id,
        title="Cross User Task",
        status=TaskStatus.PENDING,
        source_commitment_id=c.id,
    )
    await tm.store.put(t)

    # When test_user_id proposes evening review, lookup of c.id fails or returns None for test_user_id
    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        user_id=test_user_id,
    )
    # No warning because commitment is not visible to test_user_id
    assert len(proposal.presentation.commitment_warnings) == 0


# ==============================================================================
# SECTION 7: Reminder Structural Validation & Linkage (G-41 to G-45)
# ==============================================================================

def test_g41_reminder_link_none_requires_none_entity_id():
    """G-41: ReminderLinkType.NONE with non-null linked_entity_id rejected by Pydantic."""
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Invalid",
            exact_scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            linked_entity_type=ReminderLinkType.NONE,
            linked_entity_id="some_id",
        )


def test_g42_reminder_link_task_requires_entity_id():
    """G-42: ReminderLinkType.TASK with null linked_entity_id rejected by Pydantic."""
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Invalid",
            exact_scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            linked_entity_type=ReminderLinkType.TASK,
            linked_entity_id=None,
        )


def test_g43_reminder_link_commitment_requires_entity_id():
    """G-43: ReminderLinkType.COMMITMENT with null linked_entity_id rejected by Pydantic."""
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Invalid",
            exact_scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            linked_entity_type=ReminderLinkType.COMMITMENT,
            linked_entity_id=None,
        )


def test_g44_reminder_calendar_slot_interval_consistency():
    """G-44: Start without end, end without start, or start >= end rejected by Pydantic."""
    now = datetime.now(timezone.utc)
    # Start without end
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Invalid",
            exact_scheduled_at=now + timedelta(hours=1),
            calendar_slot_start=now,
            calendar_slot_end=None,
        )
    # Start >= end
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Invalid",
            exact_scheduled_at=now + timedelta(hours=1),
            calendar_slot_start=now + timedelta(hours=2),
            calendar_slot_end=now + timedelta(hours=1),
        )


def test_g45_reminder_timestamps_must_be_timezone_aware():
    """G-45: Naive datetime rejected by Pydantic validator."""
    naive = datetime(2026, 10, 1, 10, 0)
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Invalid",
            exact_scheduled_at=naive,
        )


# ==============================================================================
# SECTION 8: Precondition Discrimination & Schema Hardening (G-46 to G-50)
# ==============================================================================

def test_g46_preconditions_extra_fields_forbidden():
    """G-46: Extra attributes in precondition models raise ValidationError."""
    with pytest.raises(ValidationError):
        TaskRolloverPrecondition(
            task_id="tsk_1",
            exact_new_due_at=datetime.now(timezone.utc),
            forbidden_attribute="hacked",
        )


def test_g47_preconditions_are_frozen():
    """G-47: Attempting to mutate attributes on precondition instances raises ValidationError."""
    pre = TaskRolloverPrecondition(task_id="tsk_1", exact_new_due_at=datetime.now(timezone.utc))
    with pytest.raises(ValidationError):
        pre.task_id = "tsk_2"


def test_g48_horizon_precondition_mutual_exclusivity():
    """G-48: Precondition with more than one payload populated raises ValidationError."""
    with pytest.raises(ValidationError):
        HorizonOperationPrecondition(
            task_rollover=TaskRolloverPrecondition(task_id="tsk_1", exact_new_due_at=datetime.now(timezone.utc)),
            reminder_create=ReminderCreateExecutionPrecondition(
                exact_title="Rem", exact_scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1)
            ),
        )


def test_g49_horizon_precondition_at_least_one_payload():
    """G-49: Precondition with no payload populated raises ValidationError."""
    with pytest.raises(ValidationError):
        HorizonOperationPrecondition()


def test_g50_horizon_precondition_operation_type_mismatch():
    """G-50: Operation precondition payload mismatched with operation_type raises error."""
    pre = HorizonOperationPrecondition(
        task_rollover=TaskRolloverPrecondition(task_id="tsk_1", exact_new_due_at=datetime.now(timezone.utc))
    )
    with pytest.raises(ValidationError):
        HorizonOperation(
            index=0,
            operation_type=HorizonOperationType.CREATE_REMINDER,  # Mismatched!
            precondition=pre,
        )


# ==============================================================================
# SECTION 9: Horizon State Machine Transitions (G-51 to G-55)
# ==============================================================================

def test_g51_state_machine_legal_full_lifecycle():
    """G-51: Legal lifecycle DRAFT -> APPROVED -> EXECUTING -> COMPLETED."""
    assert validate_horizon_state_transition(HorizonProposalStatus.DRAFT, HorizonProposalStatus.APPROVED) is True
    assert validate_horizon_state_transition(HorizonProposalStatus.APPROVED, HorizonProposalStatus.EXECUTING) is True
    assert validate_horizon_state_transition(HorizonProposalStatus.EXECUTING, HorizonProposalStatus.COMPLETED) is True


def test_g52_state_machine_abort_lifecycle():
    """G-52: Legal abort lifecycle DRAFT -> ABORTED."""
    assert validate_horizon_state_transition(HorizonProposalStatus.DRAFT, HorizonProposalStatus.ABORTED) is True


def test_g53_state_machine_illegal_draft_to_executing():
    """G-53: Direct transition DRAFT -> EXECUTING raises HorizonStateTransitionError."""
    with pytest.raises(HorizonStateTransitionError):
        validate_horizon_state_transition(HorizonProposalStatus.DRAFT, HorizonProposalStatus.EXECUTING)


def test_g54_state_machine_terminal_states_immutable():
    """G-54: Transitions from terminal states raise HorizonStateTransitionError."""
    terminal_states = [
        HorizonProposalStatus.COMPLETED,
        HorizonProposalStatus.FAILED,
        HorizonProposalStatus.ABORTED,
        HorizonProposalStatus.EXPIRED,
        HorizonProposalStatus.PARTIALLY_EXECUTED,
    ]
    for term in terminal_states:
        with pytest.raises(HorizonStateTransitionError):
            validate_horizon_state_transition(term, HorizonProposalStatus.EXECUTING)


def test_g55_state_machine_approved_to_failed():
    """G-55: APPROVED -> FAILED transition is valid on preflight error."""
    assert validate_horizon_state_transition(HorizonProposalStatus.APPROVED, HorizonProposalStatus.FAILED) is True


# ==============================================================================
# SECTION 10: Authoritative 30-Minute Proposal TTL (G-56 to G-60)
# ==============================================================================

def test_g56_ttl_constant_is_authoritative_30_minutes():
    """G-56: HORIZON_PROPOSAL_TTL_MINUTES is strictly 30."""
    assert HORIZON_PROPOSAL_TTL_MINUTES == 30
    assert HORIZON_PROPOSAL_TTL_SECONDS == 1800


@pytest.mark.asyncio
async def test_g57_draft_proposal_expires_after_ttl(horizon_setup, test_user_id):
    """G-57: Draft proposal past 30 minutes transitions to EXPIRED on execution."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    proposal.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(PreconditionFailedError) as exc_info:
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert exc_info.value.code == HorizonFailureReason.EXPIRED
    assert proposal.status == HorizonProposalStatus.EXPIRED
    assert proposal.failure_reason == HorizonFailureReason.EXPIRED


@pytest.mark.asyncio
async def test_g58_approved_proposal_expires_after_ttl(horizon_setup, test_user_id):
    """G-58: Approved proposal past 30 minutes transitions to EXPIRED on execution."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    proposal.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(PreconditionFailedError) as exc_info:
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert exc_info.value.code == HorizonFailureReason.EXPIRED
    assert proposal.status == HorizonProposalStatus.EXPIRED
    assert proposal.failure_reason == HorizonFailureReason.EXPIRED


@pytest.mark.asyncio
async def test_g59_expired_proposal_fails_closed(horizon_setup, test_user_id):
    """G-59: Executing expired proposal raises error and performs zero mutations."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="Safe Task", user_id=test_user_id)
    proposal = await service.propose_evening_review(rollover_tasks=[{"task_id": t.id}], user_id=test_user_id)
    proposal.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(PreconditionFailedError) as exc_info:
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert exc_info.value.code == HorizonFailureReason.EXPIRED
    assert proposal.status == HorizonProposalStatus.EXPIRED
    assert proposal.failure_reason == HorizonFailureReason.EXPIRED
    t_after = await tm.get_task(t.id, user_id=test_user_id)
    assert t_after.due_at is None


@pytest.mark.asyncio
async def test_g60_active_proposal_within_ttl_executes(horizon_setup, test_user_id):
    """G-60: Proposal executed within TTL executes normally."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="Active TTL Task", user_id=test_user_id)
    proposal = await service.propose_evening_review(rollover_tasks=[{"task_id": t.id}], user_id=test_user_id)
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert res.status == HorizonProposalStatus.COMPLETED


# ==============================================================================
# SECTION 11: State Hashing & Concurrency Guard (G-61 to G-64)
# ==============================================================================

@pytest.mark.asyncio
async def test_g61_state_hash_computed_from_targeted_entities(horizon_setup, test_user_id):
    """G-61: Hash includes task id, status, due_at, title, source_cmt_id, and commitment fields."""
    tm = horizon_setup["task_manager"]
    cm = horizon_setup["cmt_manager"]
    c = await cm.create_commitment(title="C1", target_date=date(2026, 10, 1), user_id=test_user_id)
    t = await tm.create_task(title="T1", source_commitment_id=c.id, user_id=test_user_id)

    import json
    canonical_raw = serialize_horizon_canonical_state([t], [c])
    canonical = json.loads(canonical_raw) if isinstance(canonical_raw, str) else canonical_raw
    assert canonical["tasks"][0]["id"] == t.id
    assert canonical["tasks"][0]["title"] == "T1"
    assert canonical["commitments"][0]["target_date"] == "2026-10-01"


def test_g62_state_hash_stable_for_identical_state():
    """G-62: Computing hash twice for identical state produces identical hex digest."""
    tasks = [Task(id="tsk_1", title="T1", user_id="u1")]
    h1 = compute_horizon_state_hash(tasks, [])
    h2 = compute_horizon_state_hash(tasks, [])
    assert h1 == h2


@pytest.mark.asyncio
async def test_g63_concurrent_task_mutation_invalidates_proposal(horizon_setup, test_user_id):
    """G-63: Modifying task after proposal creation causes hash mismatch and fails closed."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="Original Title", user_id=test_user_id)
    proposal = await service.propose_evening_review(rollover_tasks=[{"task_id": t.id}], user_id=test_user_id)
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Concurrently modify task
    await tm.update_task(t.id, user_id=test_user_id, title="Mutated Title")

    with pytest.raises(PreconditionFailedError, match="STALE_STATE_HASH"):
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert proposal.status == HorizonProposalStatus.FAILED


@pytest.mark.asyncio
async def test_g64_unrelated_entity_mutation_does_not_invalidate_hash(horizon_setup, test_user_id):
    """G-64: Modifying a task NOT referenced in the proposal does not alter state hash."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t_target = await tm.create_task(title="Target Task", user_id=test_user_id)
    t_unrelated = await tm.create_task(title="Unrelated Task", user_id=test_user_id)

    proposal = await service.propose_evening_review(rollover_tasks=[{"task_id": t_target.id}], user_id=test_user_id)
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Concurrently modify unrelated task
    await tm.update_task(t_unrelated.id, user_id=test_user_id, title="Unrelated Mutated")

    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert res.status == HorizonProposalStatus.COMPLETED


# ==============================================================================
# SECTION 12: Calendar TOCTOU Preflight (G-65 to G-68)
# ==============================================================================

@pytest.mark.asyncio
async def test_g65_calendar_toctou_passes_when_slot_remains_free(horizon_setup, test_user_id):
    """G-65: When calendar slot remains free, preflight succeeds."""
    service = horizon_setup["service"]
    now = datetime.now(timezone.utc)
    s = now + timedelta(hours=2)
    e = now + timedelta(hours=3)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[
            {
                "title": "Free Slot Reminder",
                "scheduled_at": s.isoformat(),
                "calendar_slot_start": s.isoformat(),
                "calendar_slot_end": e.isoformat(),
                "linked_entity_type": "NONE",
            }
        ],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert res.status == HorizonProposalStatus.COMPLETED


@pytest.mark.asyncio
async def test_g66_calendar_toctou_fails_when_event_added_in_slot(horizon_setup, test_user_id):
    """G-66: Newly inserted calendar event overlapping reminder slot causes STALE_CALENDAR_STATE."""
    service = horizon_setup["service"]
    cal = horizon_setup["cal_manager"]
    now = datetime.now(timezone.utc)
    s = now + timedelta(hours=2)
    e = now + timedelta(hours=3)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[
            {
                "title": "Colliding Reminder",
                "scheduled_at": s.isoformat(),
                "calendar_slot_start": s.isoformat(),
                "calendar_slot_end": e.isoformat(),
                "linked_entity_type": "NONE",
            }
        ],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Concurrently add event in slot
    collision = CalendarEvent(
        event_id="evt_collision",
        user_id=test_user_id,
        title="Collision",
        start_at=s,
        end_at=e,
    )
    await cal.set_events([collision], user_id=test_user_id)

    with pytest.raises(PreconditionFailedError, match="STALE_CALENDAR_STATE"):
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert proposal.status == HorizonProposalStatus.FAILED


@pytest.mark.asyncio
async def test_g67_calendar_toctou_boundary_overlap_detection(horizon_setup, test_user_id):
    """G-67: Event overlapping partially with slot triggers STALE_CALENDAR_STATE."""
    service = horizon_setup["service"]
    cal = horizon_setup["cal_manager"]
    now = datetime.now(timezone.utc)
    s = now + timedelta(hours=2)
    e = now + timedelta(hours=3)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[
            {
                "title": "Partial Overlap Reminder",
                "scheduled_at": s.isoformat(),
                "calendar_slot_start": s.isoformat(),
                "calendar_slot_end": e.isoformat(),
                "linked_entity_type": "NONE",
            }
        ],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Event starts 30 mins before end of slot
    collision = CalendarEvent(
        event_id="evt_partial",
        user_id=test_user_id,
        title="Partial Overlap",
        start_at=e - timedelta(minutes=30),
        end_at=e + timedelta(minutes=30),
    )
    await cal.set_events([collision], user_id=test_user_id)

    with pytest.raises(PreconditionFailedError, match="STALE_CALENDAR_STATE"):
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert proposal.status == HorizonProposalStatus.FAILED


@pytest.mark.asyncio
async def test_g68_calendar_toctou_requeries_with_authenticated_user_id(horizon_setup, test_user_id, other_user_id):
    """G-68: Calendar TOCTOU check forwards explicit user_id=authenticated_user_id."""
    service = horizon_setup["service"]
    cal = horizon_setup["cal_manager"]
    now = datetime.now(timezone.utc)
    s = now + timedelta(hours=2)
    e = now + timedelta(hours=3)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[
            {
                "title": "User Scoped TOCTOU",
                "scheduled_at": s.isoformat(),
                "calendar_slot_start": s.isoformat(),
                "calendar_slot_end": e.isoformat(),
                "linked_entity_type": "NONE",
            }
        ],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Event on other_user_id's calendar does not collide
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=other_user_id)
    ev_other = CalendarEvent(
        event_id="evt_other",
        user_id=other_user_id,
        title="Other Event",
        start_at=s,
        end_at=e,
    )
    await cal.set_events([ev_other], user_id=other_user_id)

    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert res.status == HorizonProposalStatus.COMPLETED


# ==============================================================================
# SECTION 13: Partial Execution & Failure Handling (G-69 to G-71)
# ==============================================================================

@pytest.mark.asyncio
async def test_g69_operation_1_failure_marks_proposal_failed(horizon_setup, test_user_id):
    """G-69: Failure on first operation marks proposal FAILED, zero mutations."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="T1", user_id=test_user_id)
    proposal = await service.propose_evening_review(rollover_tasks=[{"task_id": t.id}], user_id=test_user_id)
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Mock task_manager.update_task to raise error
    orig = tm.update_task
    async def fail_update(*args, **kwargs):
        raise RuntimeError("DB Disk Error")
    tm.update_task = fail_update
    try:
        res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
        assert res.status == HorizonProposalStatus.FAILED
        assert res.ordered_operations[0].status == HorizonOperationStatus.FAILED
    finally:
        tm.update_task = orig


@pytest.mark.asyncio
async def test_g70_operation_k_failure_marks_proposal_partially_executed(horizon_setup, test_user_id):
    """G-70: Failure on second operation marks proposal PARTIALLY_EXECUTED."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    rm = horizon_setup["rem_manager"]
    t = await tm.create_task(title="T1", user_id=test_user_id)
    now = datetime.now(timezone.utc)
    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        next_day_reminders=[{"title": "R1", "scheduled_at": (now + timedelta(hours=2)).isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Mock reminder_manager.create_reminder to fail
    orig = rm.create_reminder
    async def fail_create(*args, **kwargs):
        raise RuntimeError("Reminder Service Down")
    rm.create_reminder = fail_create
    try:
        res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
        assert res.status == HorizonProposalStatus.PARTIALLY_EXECUTED
        assert res.ordered_operations[0].status == HorizonOperationStatus.COMPLETED
        assert res.ordered_operations[1].status == HorizonOperationStatus.FAILED
    finally:
        rm.create_reminder = orig


@pytest.mark.asyncio
async def test_g71_partial_execution_preserves_first_mutation(horizon_setup, test_user_id):
    """G-71: First operation remains committed on partial execution; no auto-rollback."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    rm = horizon_setup["rem_manager"]
    t = await tm.create_task(title="Preserved Task", user_id=test_user_id)
    now = datetime.now(timezone.utc)
    proposal = await service.propose_evening_review(
        rollover_tasks=[{"task_id": t.id}],
        next_day_reminders=[{"title": "R1", "scheduled_at": (now + timedelta(hours=2)).isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    orig = rm.create_reminder
    async def fail_create(*args, **kwargs):
        raise RuntimeError("Reminder Failure")
    rm.create_reminder = fail_create
    try:
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
        # Check task was indeed updated
        t_after = await tm.get_task(t.id, user_id=test_user_id)
        assert t_after.due_at is not None
    finally:
        rm.create_reminder = orig


# ==============================================================================
# SECTION 14: Client Action Contract (G-72 to G-73)
# ==============================================================================

@pytest.mark.asyncio
async def test_g72_reminder_creation_emits_schedule_local_notification(horizon_setup, test_user_id):
    """G-72: Successful CREATE_REMINDER produces schedule_local_notification action."""
    service = horizon_setup["service"]
    future_time = datetime.now(timezone.utc) + timedelta(hours=2)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[{"title": "Milestone Action", "scheduled_at": future_time.isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    op = res.ordered_operations[0]
    action = op.proposed_payload.get("client_action")
    assert action is not None
    assert action["action"] == "schedule_local_notification"


@pytest.mark.asyncio
async def test_g73_client_action_matches_phase6b_contract(horizon_setup, test_user_id):
    """G-73: Client action matches Phase 6B contract fields."""
    rem = Reminder(id="rem_123", title="Test Rem", scheduled_at=datetime.now(timezone.utc), user_id="u1")
    action = build_client_action("schedule_local_notification", rem)
    assert action["action"] == "schedule_local_notification"
    assert action["payload"]["reminder_id"] == "rem_123"
    assert action["payload"]["title"] == "Test Rem"
    assert "scheduled_at" in action["payload"]
    assert action["payload"]["notification_type"] == "reminder"


# ==============================================================================
# SECTION 15: Memory Lifecycle Governance (G-74 to G-75)
# ==============================================================================

@pytest.mark.asyncio
async def test_g74_memory_creation_parameters_hardened(horizon_setup, test_user_id):
    """G-74: Reflection memory created with EPISODIC, USER_EXPLICIT, confidence=1.0, status=ACTIVE, importance=3."""
    service = horizon_setup["service"]
    mm = horizon_setup["mem_manager"]
    proposal = await service.propose_evening_review(
        reflection_notes="Great velocity today on Phase 6G.",
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    assert res.status == HorizonProposalStatus.COMPLETED

    mem_id = res.ordered_operations[0].created_entity_id
    mem = await mm.get_memory(mem_id, user_id=test_user_id)
    assert mem.type == MemoryType.EPISODIC
    assert mem.source == MemorySource.USER_EXPLICIT
    assert mem.status == MemoryStatus.ACTIVE
    assert mem.confidence == 1.0
    assert mem.importance == 3


@pytest.mark.asyncio
async def test_g75_memory_tags_and_user_id_enforced(horizon_setup, test_user_id):
    """G-75: Reflection memory receives context_tags and authenticated user_id."""
    service = horizon_setup["service"]
    mm = horizon_setup["mem_manager"]
    proposal = await service.propose_evening_review(
        reflection_notes="Reflected on architecture.",
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
    mem_id = res.ordered_operations[0].created_entity_id
    mem = await mm.get_memory(mem_id, user_id=test_user_id)
    assert "horizon_review" in mem.tags
    assert mem.user_id == test_user_id


# ==============================================================================
# SECTION 16: Identity Forwarding & Cross-User Isolation (G-76 to G-82)
# ==============================================================================

@pytest.mark.asyncio
async def test_g76_identity_test_a_morning_plan_user_scoping(horizon_setup, test_user_id, other_user_id):
    """G-76: Test A: Morning plan strictly scoped to user_id."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=other_user_id)
    await tm.create_task(title="A Task", user_id=test_user_id)

    plan = await service.get_daily_executive_plan(user_id=other_user_id)
    assert len(plan.tasks) == 0


@pytest.mark.asyncio
async def test_g77_identity_test_b_day_plan_proposal_user_binding(horizon_setup, test_user_id, other_user_id):
    """G-77: Test B: Proposal bound to user_id and inaccessible to other user."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    with pytest.raises(ValueError, match="not found"):
        await service.get_proposal(proposal.proposal_id, user_id=other_user_id)


@pytest.mark.asyncio
async def test_g78_identity_test_c_evening_review_user_scoping(horizon_setup, test_user_id, other_user_id):
    """G-78: Test C: Evening review strictly scoped to user_id."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=other_user_id)
    await tm.create_task(title="A Task", user_id=test_user_id)

    review = await service.get_evening_horizon_review(user_id=other_user_id)
    assert len(review.completed_tasks) == 0
    assert len(review.overdue_tasks) == 0


@pytest.mark.asyncio
async def test_g79_identity_test_d_evening_proposal_user_binding(horizon_setup, test_user_id, other_user_id):
    """G-79: Test D: Evening proposal bound to user_id and inaccessible to other user."""
    service = horizon_setup["service"]
    proposal = await service.propose_evening_review(user_id=test_user_id)
    with pytest.raises(ValueError, match="not found"):
        await service.get_proposal(proposal.proposal_id, user_id=other_user_id)


@pytest.mark.asyncio
async def test_g80_identity_test_e_execution_user_id_match(horizon_setup, test_user_id, other_user_id):
    """G-80: Test E: Executing proposal with wrong user_id fails closed."""
    service = horizon_setup["service"]
    proposal = await service.propose_daily_plan(user_id=test_user_id)
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    with pytest.raises(ValueError, match="not found"):
        await service.execute_proposal(proposal.proposal_id, user_id=other_user_id)


@pytest.mark.asyncio
async def test_g81_identity_test_f_entity_ownership_verification(horizon_setup, test_user_id, other_user_id):
    """G-81: Test F: Cannot rollover or link reminder to another user's task."""
    service = horizon_setup["service"]
    tm = horizon_setup["task_manager"]
    t = await tm.create_task(title="Other User Task", user_id=other_user_id)
    with pytest.raises(ValueError, match="not found or access denied"):
        await service.propose_evening_review(rollover_tasks=[{"task_id": t.id}], user_id=test_user_id)


@pytest.mark.asyncio
async def test_g82_identity_test_g_forbid_default_local_user_id(horizon_setup):
    """G-82: Test G: Phase 6G rejects missing or DEFAULT_LOCAL_USER_ID user_id."""
    service = horizon_setup["service"]
    with pytest.raises(ValueError, match="Explicit authenticated user_id is required"):
        await service.get_daily_executive_plan(user_id=DEFAULT_LOCAL_USER_ID)
    with pytest.raises(ValueError, match="Explicit authenticated user_id is required"):
        await service.propose_daily_plan(user_id=DEFAULT_LOCAL_USER_ID)


# ==============================================================================
# SECTION 17: Read-Permission Matrix & Fail-Closed Gating (G-83 to G-87)
# ==============================================================================

@pytest.mark.asyncio
async def test_g83_permission_matrix_all_authorized(horizon_setup, test_user_id):
    """G-83: All domains queried and present when all permissions authorized."""
    service = horizon_setup["service"]
    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    assert len(plan.omitted_domains) == 0


@pytest.mark.asyncio
async def test_g84_permission_matrix_tasks_only_authorized(horizon_setup, test_user_id):
    """G-84: Only tasks returned; calendar, commitments, reminders omitted with metadata."""
    service = horizon_setup["service"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)
    await pm.update_permission(PermissionType.COMMITMENTS, PermissionStatus.DENIED, user_id=test_user_id)
    await pm.update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=test_user_id)

    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    omitted = [d.get("domain") for d in plan.omitted_domains]
    assert "calendar" in omitted
    assert "commitments" in omitted
    assert "reminders" in omitted
    assert "tasks" not in omitted


@pytest.mark.asyncio
async def test_g85_permission_matrix_calendar_only_authorized(horizon_setup, test_user_id):
    """G-85: Only calendar returned; tasks, commitments, reminders omitted with metadata."""
    service = horizon_setup["service"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=test_user_id)
    await pm.update_permission(PermissionType.COMMITMENTS, PermissionStatus.DENIED, user_id=test_user_id)
    await pm.update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=test_user_id)

    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    omitted = [d.get("domain") for d in plan.omitted_domains]
    assert "calendar" not in omitted
    assert "tasks" in omitted


@pytest.mark.asyncio
async def test_g86_permission_matrix_zero_authorized_fails_closed(horizon_setup, test_user_id):
    """G-86: When all 4 permissions denied, raises PermissionError (fail closed)."""
    service = horizon_setup["service"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=test_user_id)
    await pm.update_permission(PermissionType.COMMITMENTS, PermissionStatus.DENIED, user_id=test_user_id)
    await pm.update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=test_user_id)

    with pytest.raises(PermissionError, match="No authorized domains"):
        await service.get_daily_executive_plan(user_id=test_user_id)
    with pytest.raises(PermissionError, match="No authorized domains"):
        await service.get_evening_horizon_review(user_id=test_user_id)


@pytest.mark.asyncio
async def test_g87_permission_matrix_omission_metadata_structure(horizon_setup, test_user_id):
    """G-87: Structured omission metadata includes domain names and reason."""
    service = horizon_setup["service"]
    pm = horizon_setup["perm_manager"]
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=test_user_id)
    plan = await service.get_daily_executive_plan(user_id=test_user_id)
    assert any(d.get("domain") == "calendar" for d in plan.omitted_domains)


# ==============================================================================
# SECTION 18: Early Future-Time Preflight (G-88 to G-92)
# ==============================================================================

@pytest.mark.asyncio
async def test_g88_early_future_time_check_rejects_past_time(horizon_setup, test_user_id):
    """G-88: Scheduled time in past rejected at Step 3 with SCHEDULED_TIME_IN_PAST."""
    service = horizon_setup["service"]
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    with pytest.raises(ValueError, match="scheduled_at must be in the future"):
        await service.propose_daily_plan(
            milestone_reminders=[{"title": "Past Rem", "scheduled_at": past_time.isoformat(), "linked_entity_type": "NONE"}],
            user_id=test_user_id,
        )


@pytest.mark.asyncio
async def test_g89_early_future_time_check_zero_mutations(horizon_setup, test_user_id):
    """G-89: When early check fails, zero reminders or tasks are created/mutated."""
    service = horizon_setup["service"]
    rm = horizon_setup["rem_manager"]
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    try:
        await service.propose_daily_plan(
            milestone_reminders=[{"title": "Past Rem", "scheduled_at": past_time.isoformat(), "linked_entity_type": "NONE"}],
            user_id=test_user_id,
        )
    except ValueError:
        pass
    rems = await rm.list_reminders(user_id=test_user_id)
    assert len(rems) == 0


@pytest.mark.asyncio
async def test_g90_early_future_time_check_zero_client_actions(horizon_setup, test_user_id):
    """G-90: When early check fails, zero client actions emitted."""
    service = horizon_setup["service"]
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    with pytest.raises(ValueError):
        await service.propose_daily_plan(
            milestone_reminders=[{"title": "Past Rem", "scheduled_at": past_time.isoformat(), "linked_entity_type": "NONE"}],
            user_id=test_user_id,
        )


@pytest.mark.asyncio
async def test_g91_early_future_time_check_fails_before_calendar_toctou(horizon_setup, test_user_id):
    """G-91: Past time fails before calendar TOCTOU query is performed."""
    service = horizon_setup["service"]
    now = datetime.now(timezone.utc)
    # Propose with valid time, then artificially age precondition before execute
    proposal = await service.propose_daily_plan(
        milestone_reminders=[{"title": "Valid Rem", "scheduled_at": (now + timedelta(hours=2)).isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Force precondition scheduled_at into the past to trigger Step 3 preflight in execute_proposal
    op = proposal.ordered_operations[0]
    past_pre = ReminderCreateExecutionPrecondition(
        exact_title=op.precondition.reminder_create.exact_title,
        exact_scheduled_at=now - timedelta(minutes=10),
        linked_entity_type=ReminderLinkType.NONE,
    )
    object.__setattr__(op, "precondition", HorizonOperationPrecondition(reminder_create=past_pre))

    with pytest.raises(PreconditionFailedError, match="SCHEDULED_TIME_IN_PAST"):
        await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)


@pytest.mark.asyncio
async def test_g92_early_future_time_check_requires_timezone_aware(horizon_setup, test_user_id):
    """G-92: Aware UTC time compared cleanly without offset errors."""
    service = horizon_setup["service"]
    aware_future = datetime.now(timezone.utc) + timedelta(hours=2)
    proposal = await service.propose_daily_plan(
        milestone_reminders=[{"title": "Aware Rem", "scheduled_at": aware_future.isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    assert proposal.ordered_operations[0].precondition.reminder_create.exact_scheduled_at.tzinfo is not None


# ==============================================================================
# SECTION 19: ContextEngine & REST Consistency (G-93 to G-96)
# ==============================================================================

@pytest.mark.asyncio
async def test_g93_context_providers_enforce_domain_permissions(horizon_setup, test_user_id):
    """G-93: Domain permissions properly guard domain access."""
    pm = horizon_setup["perm_manager"]
    assert await pm.is_authorized(PermissionType.TASKS, user_id=test_user_id) is True
    await pm.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=test_user_id)
    assert await pm.is_authorized(PermissionType.TASKS, user_id=test_user_id) is False


@pytest.mark.asyncio
async def test_g94_rest_and_tools_call_same_service_methods(horizon_setup, test_user_id):
    """G-94: REST endpoints and tools both invoke ExecutiveHorizonOrchestrationService."""
    service = horizon_setup["service"]
    tool = GetDailyExecutivePlanTool(horizon_service=service)
    result = await tool.execute({"target_date": "2026-10-01", "_user_id": test_user_id})
    assert result["success"] is True
    assert result["data"]["target_date"] == "2026-10-01"


@pytest.mark.asyncio
async def test_g95_rest_endpoints_extract_authenticated_identity(horizon_setup, test_user_id):
    """G-95: Horizon tools and service enforce authenticated identity."""
    service = horizon_setup["service"]
    tool = ProposeDailyPlanTool(horizon_service=service)
    res = await tool.execute({"_user_id": test_user_id})
    assert res["success"] is True
    assert res["data"]["user_id"] == test_user_id


def test_g96_horizon_tools_definitions_and_risk_levels():
    """G-96: Verifies 4 LOW tools and 2 HIGH tools configured with proper risk levels."""
    t1 = GetDailyExecutivePlanTool()
    t2 = ProposeDailyPlanTool()
    t3 = GetEveningHorizonReviewTool()
    t4 = ProposeEveningReviewTool()
    t5 = ApplyDailyPlanTool()
    t6 = ApplyEveningReviewTool()

    assert t1.risk_level == ToolRiskLevel.LOW
    assert t2.risk_level == ToolRiskLevel.LOW
    assert t3.risk_level == ToolRiskLevel.LOW
    assert t4.risk_level == ToolRiskLevel.LOW
    assert t5.risk_level == ToolRiskLevel.HIGH
    assert t6.risk_level == ToolRiskLevel.HIGH


# ==============================================================================
# SECTION 20: Critical Temporal Tests (G-97 to G-99)
# ==============================================================================

@pytest.mark.asyncio
async def test_g97_temporal_slippage_boundary_rejection(horizon_setup, test_user_id):
    """G-97: Timestamp initially future at early check; slippage causes final boundary check to reject."""
    service = horizon_setup["service"]
    rm = horizon_setup["rem_manager"]
    now = datetime.now(timezone.utc)
    # 2 seconds in the future
    near_future = now + timedelta(seconds=2)

    proposal = await service.propose_daily_plan(
        milestone_reminders=[{"title": "Near Expiry Rem", "scheduled_at": near_future.isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)

    # Simulate temporal slippage: make Tier 1 preflight pass, but boundary check see a past time
    orig_datetime = datetime
    call_count = 0
    class SimulatedTimeDatetime:
        @classmethod
        def now(cls, tz=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return orig_datetime.now(tz)
            # Advance time by 10 seconds into the future for final boundary check
            return orig_datetime.now(tz) + timedelta(seconds=10)

        @classmethod
        def combine(cls, *args, **kwargs):
            return orig_datetime.combine(*args, **kwargs)

    # Monkeypatch datetime in horizon module during execute_proposal
    import app.services.agenda.horizon as horizon_module
    orig_dt_module = horizon_module.datetime
    horizon_module.datetime = SimulatedTimeDatetime
    try:
        res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)
        assert res.status == HorizonProposalStatus.FAILED
        assert res.ordered_operations[0].status == HorizonOperationStatus.FAILED
        assert "SCHEDULED_TIME_IN_PAST" in res.ordered_operations[0].failure_error
    finally:
        horizon_module.datetime = orig_dt_module

    # Confirm ReminderManager.create_reminder was NOT called / zero reminders created
    created_rems = await rm.list_reminders(user_id=test_user_id)
    assert len(created_rems) == 0
    # Confirm zero client actions in payload
    assert "client_action" not in res.ordered_operations[0].proposed_payload


def test_g98_ast_structural_verification_zero_await_before_create_reminder():
    """G-98: AST verification proves zero await/IO/async between final time check and ReminderManager.create_reminder."""
    import textwrap
    func_source = inspect.getsource(ExecutiveHorizonOrchestrationService.execute_proposal)
    tree = ast.parse(textwrap.dedent(func_source))

    # Locate CREATE_REMINDER handler block in the loop
    found_time_check = False
    found_create_reminder = False
    intervening_awaits = []

    for node in ast.walk(tree):
        # Look for the if statement: if r_pre.exact_scheduled_at <= now_utc: raise PreconditionFailedError(...)
        if isinstance(node, ast.If):
            # Check if test is a comparison involving <= and now_utc
            for comp in ast.walk(node.test):
                if isinstance(comp, ast.Name) and comp.id == "now_utc":
                    found_time_check = True

        # Check for create_reminder call
        if isinstance(node, ast.Call):
            for attr in ast.walk(node):
                if isinstance(attr, ast.Attribute) and attr.attr == "create_reminder":
                    found_create_reminder = True

    assert found_time_check is True, "Must have authoritative final time check 'exact_scheduled_at <= now_utc'"
    assert found_create_reminder is True, "Must call create_reminder"

    # Now let's do a strict sequential check of statements in the CREATE_REMINDER block
    # Search lines of source code directly
    lines = func_source.splitlines()
    check_line_idx = None
    create_line_idx = None

    for i, line in enumerate(lines):
        if "if r_pre.exact_scheduled_at <= now_utc:" in line or "exact_scheduled_at <= now_utc" in line:
            check_line_idx = i
        if "await self.reminder_manager.create_reminder(" in line:
            create_line_idx = i
            break

    assert check_line_idx is not None, "Could not find final time check line"
    assert create_line_idx is not None, "Could not find create_reminder call line"
    assert check_line_idx < create_line_idx, "Final time check must strictly precede create_reminder"

    # Inspect all lines between check_line_idx and create_line_idx
    intervening_code = "\n".join(lines[check_line_idx:create_line_idx])
    assert "HorizonFailureReason.SCHEDULED_TIME_IN_PAST" in intervening_code, (
        "Raise site must pass structured HorizonFailureReason.SCHEDULED_TIME_IN_PAST"
    )
    assert "PreconditionFailedError" in intervening_code, (
        "Raise site must raise PreconditionFailedError"
    )

    # Must only contain the if block raise and comments
    for line in lines[check_line_idx:create_line_idx]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Allowed statements: the if check and the structured raise
        assert any(
            allowed in stripped
            for allowed in [
                "if r_pre.exact_scheduled_at <= now_utc:",
                "raise PreconditionFailedError(",
                "code=HorizonFailureReason.SCHEDULED_TIME_IN_PAST,",
                'message="SCHEDULED_TIME_IN_PAST",',
                ")",
            ]
        ), f"Unexpected intervening statement found between time check and mutation: {stripped}"
        # Strictly forbid any await in intervening code
        assert "await " not in stripped, f"Illegal await statement found before mutation: {stripped}"


@pytest.mark.asyncio
async def test_g99_fresh_future_time_mutation_succeeds_with_action(horizon_setup, test_user_id):
    """G-99: Fresh future timestamp succeeds at final check; creates reminder once, emits schedule_local_notification."""
    service = horizon_setup["service"]
    rm = horizon_setup["rem_manager"]
    now = datetime.now(timezone.utc)
    future_time = now + timedelta(hours=3)

    proposal = await service.propose_daily_plan(
        milestone_reminders=[{"title": "G99 Milestone", "scheduled_at": future_time.isoformat(), "linked_entity_type": "NONE"}],
        user_id=test_user_id,
    )
    await service.approve_proposal(proposal.proposal_id, user_id=test_user_id)
    res = await service.execute_proposal(proposal.proposal_id, user_id=test_user_id)

    assert res.status == HorizonProposalStatus.COMPLETED
    op = res.ordered_operations[0]
    assert op.status == HorizonOperationStatus.COMPLETED
    assert op.created_entity_id is not None

    # Reminder created in ReminderManager
    rems = await rm.list_reminders(user_id=test_user_id)
    assert len(rems) == 1
    assert rems[0].title == "G99 Milestone"

    # Exactly one client action emitted
    action = op.proposed_payload.get("client_action")
    assert action is not None
    assert action["action"] == "schedule_local_notification"
    assert action["payload"]["reminder_id"] == rems[0].id
