import pytest
import pytest_asyncio
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional
from pydantic import ValidationError

from app.main import app
from app.services.calendar.models import CalendarEvent
from app.services.calendar.manager import CalendarManager
from app.services.tasks import Task, TaskStatus, TaskPriority, TaskManager, InMemoryTaskStore
from app.services.commitments import Commitment, CommitmentStatus, CommitmentPriority, CommitmentManager, InMemoryCommitmentStore
from app.services.reminders import Reminder, ReminderManager, InMemoryReminderStore
from app.services.memory.schemas import MemoryItem, MemoryType, MemorySource, MemoryStatus
from app.services.memory.manager import MemoryManager
from app.services.memory.store import InMemoryMemoryStore
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore

from app.services.agenda.briefing_models import (
    DebriefProposalStatus,
    DebriefFailureReason,
    ReminderLinkType,
    DebriefOperationType,
    DebriefOperationStatus,
    DebriefStateTransitionError,
    TaskCreateExecutionPrecondition,
    ReminderCreateExecutionPrecondition,
    MemoryCreateExecutionPrecondition,
    DebriefOperationPrecondition,
    DebriefOperation,
    DebriefProposal,
    EventBriefing,
    DEBRIEF_PROPOSAL_TTL_MINUTES,
    DEBRIEF_PROPOSAL_TTL_SECONDS,
    validate_debrief_state_transition,
    serialize_canonical_state,
    compute_debrief_state_hash,
)
from app.services.agenda.briefing import EventBriefingService
from app.services.tools.builtins.briefing_tool import (
    GenerateEventBriefingTool,
    ProposeEventDebriefTool,
    ApplyEventDebriefTool,
)


# ── Fixtures & Setup Helpers ──────────────────────────────────────────────────

@pytest.fixture
def test_user_id() -> str:
    return f"usr_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def other_user_id() -> str:
    return f"usr_other_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def briefing_service(test_user_id) -> EventBriefingService:
    # Permission Manager (Authorized by default)
    perm_manager = PermissionManager(store=InMemoryPermissionStore())
    await perm_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=test_user_id)
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=test_user_id)

    # Calendar
    cal_manager = CalendarManager(permission_manager=perm_manager)

    # Ingest a sample event for test_user_id
    now = datetime.now(timezone.utc)
    ev = CalendarEvent(
        event_id="evt_sync_123",
        user_id=test_user_id,
        title="Product Roadmap Sync",
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        location="Room 402",
    )
    await cal_manager.set_events([ev], user_id=test_user_id)

    # Task Manager
    task_store = InMemoryTaskStore()
    task_manager = TaskManager(store=task_store)

    # Commitment Manager
    cmt_store = InMemoryCommitmentStore()
    cmt_manager = CommitmentManager(store=cmt_store)

    # Reminder Manager
    rem_store = InMemoryReminderStore()
    rem_manager = ReminderManager(store=rem_store, task_manager=task_manager, commitment_manager=cmt_manager)

    # Memory Manager
    mem_store = InMemoryMemoryStore()
    mem_manager = MemoryManager(store=mem_store)

    service = EventBriefingService(
        calendar_manager=cal_manager,
        task_manager=task_manager,
        commitment_manager=cmt_manager,
        reminder_manager=rem_manager,
        memory_manager=mem_manager,
        permission_manager=perm_manager,
    )
    return service


# ==============================================================================
# BASELINE PHASE 6F TESTS (F-01 through F-51)
# ==============================================================================

@pytest.mark.asyncio
async def test_f01_event_briefing_synthesis_success(briefing_service, test_user_id):
    """F-01: Generates read-only briefing for valid calendar event."""
    briefing = await briefing_service.generate_briefing(event_id="evt_sync_123", user_id=test_user_id)
    assert briefing.event_id == "evt_sync_123"
    assert briefing.title == "Product Roadmap Sync"
    assert "Briefing for 'Product Roadmap Sync'" in briefing.summary


@pytest.mark.asyncio
async def test_f02_event_briefing_nonexistent_event_fails(briefing_service, test_user_id):
    """F-02: Non-existent event raises ValueError."""
    with pytest.raises(ValueError, match="not found"):
        await briefing_service.generate_briefing(event_id="evt_nonexistent", user_id=test_user_id)


@pytest.mark.asyncio
async def test_f03_event_briefing_user_scoping(briefing_service, other_user_id):
    """F-03: Cross-user briefing generation fails closed."""
    with pytest.raises(ValueError, match="not found"):
        await briefing_service.generate_briefing(event_id="evt_sync_123", user_id=other_user_id)


@pytest.mark.asyncio
async def test_f04_event_briefing_correlates_tasks_and_commitments(briefing_service, test_user_id):
    """F-04: Briefing correlates related tasks and active commitments by keyword."""
    # Seed related task
    await briefing_service.task_manager.create_task(
        title="Prepare Product Roadmap slides",
        user_id=test_user_id,
    )
    # Seed related commitment
    await briefing_service.commitment_manager.create_commitment(
        title="Deliver Product Roadmap v2",
        user_id=test_user_id,
        target_date=datetime.now(timezone.utc) + timedelta(days=3),
    )

    briefing = await briefing_service.generate_briefing(event_id="evt_sync_123", user_id=test_user_id)
    assert len(briefing.related_tasks) == 1
    assert briefing.related_tasks[0]["title"] == "Prepare Product Roadmap slides"
    assert len(briefing.active_commitments) == 1
    assert briefing.active_commitments[0]["title"] == "Deliver Product Roadmap v2"


@pytest.mark.asyncio
async def test_f11_propose_debrief_creates_draft_proposal(briefing_service, test_user_id):
    """F-11: propose_debrief creates server-held proposal in DRAFT state with TTL."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        raw_notes="Agreed to complete API integration by next Tuesday.",
        task_candidates=[{"exact_title": "Complete API integration", "exact_duration_minutes": 60}],
    )
    assert proposal.status == DebriefProposalStatus.DRAFT
    assert proposal.event_id == "evt_sync_123"
    assert len(proposal.ordered_operations) >= 1
    assert proposal.expires_at > datetime.now(timezone.utc)
    assert len(proposal.state_hash) == 64  # SHA-256


@pytest.mark.asyncio
async def test_f12_propose_debrief_presentation_payload_separation(briefing_service, test_user_id):
    """F-12: proposed_payload is presentation-only, execution uses precondition."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[{"exact_title": "Fix bug 404", "exact_duration_minutes": 45}],
    )
    op = proposal.ordered_operations[0]
    assert op.operation_type == DebriefOperationType.CREATE_TASK
    assert op.proposed_payload["title"] == "Fix bug 404"
    assert op.precondition.task_create.exact_title == "Fix bug 404"
    assert op.precondition.task_create.exact_duration_minutes == 45


@pytest.mark.asyncio
async def test_f21_discriminated_precondition_mutual_exclusivity():
    """F-21: DebriefOperationPrecondition enforces mutual exclusivity."""
    tc = TaskCreateExecutionPrecondition(exact_title="Test Task")
    rc = ReminderCreateExecutionPrecondition(exact_title="Test Reminder", exact_scheduled_at=datetime.now(timezone.utc))

    # Valid task precondition
    p_task = DebriefOperationPrecondition(operation_type=DebriefOperationType.CREATE_TASK, task_create=tc)
    assert p_task.task_create is not None
    assert p_task.reminder_create is None

    # Invalid: CREATE_TASK with reminder_create populated
    with pytest.raises(ValidationError):
        DebriefOperationPrecondition(
            operation_type=DebriefOperationType.CREATE_TASK,
            task_create=tc,
            reminder_create=rc,
        )


@pytest.mark.asyncio
async def test_f31_reminder_link_type_none_requires_null_id():
    """F-31: ReminderLinkType.NONE requires linked_entity_id to be None."""
    # Valid
    ReminderCreateExecutionPrecondition(
        exact_title="Check mail",
        exact_scheduled_at=datetime.now(timezone.utc),
        linked_entity_type=ReminderLinkType.NONE,
        linked_entity_id=None,
    )
    # Invalid: linked_entity_id populated when NONE
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Check mail",
            exact_scheduled_at=datetime.now(timezone.utc),
            linked_entity_type=ReminderLinkType.NONE,
            linked_entity_id="tsk_123",
        )


@pytest.mark.asyncio
async def test_f32_reminder_link_type_task_requires_id():
    """F-32: ReminderLinkType.TASK requires non-empty linked_entity_id."""
    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(
            exact_title="Follow up on task",
            exact_scheduled_at=datetime.now(timezone.utc),
            linked_entity_type=ReminderLinkType.TASK,
            linked_entity_id=None,
        )


@pytest.mark.asyncio
async def test_f41_execute_proposal_all_operations_succeed(briefing_service, test_user_id):
    """F-41: apply proposal executes tasks, reminders, memories and completes."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        raw_notes="Great discussion on Q4 goals.",
        task_candidates=[{"exact_title": "Write Q4 summary", "exact_duration_minutes": 30}],
        reminder_candidates=[{
            "exact_title": "Remind Q4 review",
            "exact_scheduled_at": datetime.now(timezone.utc) + timedelta(days=1),
            "linked_entity_type": "none",
        }],
        memory_candidates=[{
            "exact_content": "Team aligned on Q4 objectives.",
            "exact_memory_type": "episodic",
        }],
    )

    res = await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=True)
    assert res.status == DebriefProposalStatus.COMPLETED
    assert res.applied_at is not None
    assert all(op.status == DebriefOperationStatus.COMPLETED for op in res.ordered_operations)
    assert res.ordered_operations[0].created_entity_id.startswith("tsk_")
    assert res.ordered_operations[1].created_entity_id.startswith("rem_")
    assert res.ordered_operations[2].created_entity_id.startswith("mem_")


# ==============================================================================
# TEMPORAL SEMANTICS & STATE HASH TESTS (F-52 through F-56)
# ==============================================================================

def test_f52_commitment_target_date_date_only_hash():
    """F-52: Commitment.target_date serializes strictly as date-only YYYY-MM-DD in state hash."""
    target_dt = datetime(2026, 10, 15, 14, 30, 0, tzinfo=timezone.utc)
    c = Commitment(
        id="cmt_abc",
        title="Deliver report",
        status=CommitmentStatus.ACTIVE,
        target_date=target_dt,
    )
    serialized = serialize_canonical_state(tasks=[], commitments=[c])
    assert '"target_date":"2026-10-15"' in serialized
    assert "T" not in serialized.split('"target_date":')[1].split(',')[0]  # No ISO datetime instant


def test_f53_commitment_target_date_no_midnight_conversion():
    """F-53: Commitment.target_date does NOT convert to fabricated midnight UTC string."""
    target_dt = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    c = Commitment(id="cmt_eoy", title="EOY Wrap", target_date=target_dt)
    serialized = serialize_canonical_state(tasks=[], commitments=[c])
    assert '"target_date":"2026-12-31"' in serialized
    assert "00:00:00" not in serialized


def test_f54_proposal_and_execution_hash_determinism():
    """F-54: Proposal and execution preflight produce identical deterministic SHA-256 hash."""
    t1 = Task(id="tsk_1", title="Task 1", due_at=datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc))
    c1 = Commitment(id="cmt_1", title="Commitment 1", target_date=datetime(2026, 5, 2, 12, 0, 0, tzinfo=timezone.utc))

    hash_a = compute_debrief_state_hash([t1], [c1])
    hash_b = compute_debrief_state_hash([t1], [c1])
    assert hash_a == hash_b
    assert len(hash_a) == 64


def test_f55_task_due_at_utc_instant_hash():
    """F-55: Task.due_at serializes as full UTC ISO-8601 instant."""
    due = datetime(2026, 6, 15, 9, 30, 0, tzinfo=timezone.utc)
    t = Task(id="tsk_due", title="Submit taxes", due_at=due)
    serialized = serialize_canonical_state(tasks=[t], commitments=[])
    assert '"due_at":"2026-06-15T09:30:00+00:00"' in serialized


def test_f56_temporal_null_field_hashing():
    """F-56: None temporal fields serialize strictly as JSON null."""
    t = Task(id="tsk_null", title="No due date", due_at=None)
    c = Commitment(id="cmt_null", title="No target date", target_date=None)
    serialized = serialize_canonical_state(tasks=[t], commitments=[c])
    assert '"due_at":null' in serialized
    assert '"target_date":null' in serialized


# ==============================================================================
# AUTHORITATIVE STATE MACHINE TRANSITION TESTS (F-57 through F-68)
# ==============================================================================

@pytest.mark.asyncio
async def test_f57_proposal_creation_initial_draft_state(briefing_service, test_user_id):
    """F-57: propose_debrief initializes in DRAFT."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        raw_notes="Sync notes",
    )
    assert proposal.status == DebriefProposalStatus.DRAFT


@pytest.mark.asyncio
async def test_f58_transition_draft_to_approved_on_user_confirm(briefing_service, test_user_id):
    """F-58: User confirmation transitions DRAFT -> APPROVED."""
    proposal = await briefing_service.propose_debrief(event_id="evt_sync_123", user_id=test_user_id, raw_notes="Notes")
    validate_debrief_state_transition(proposal.status, DebriefProposalStatus.APPROVED)


@pytest.mark.asyncio
async def test_f59_transition_draft_to_aborted_on_user_rejection(briefing_service, test_user_id):
    """F-59: User rejection transitions DRAFT -> ABORTED."""
    proposal = await briefing_service.propose_debrief(event_id="evt_sync_123", user_id=test_user_id, raw_notes="Notes")
    aborted = await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=False)
    assert aborted.status == DebriefProposalStatus.ABORTED


@pytest.mark.asyncio
async def test_f60_transition_approved_to_executing_on_preflight_success(briefing_service, test_user_id):
    """F-60: Preflight success transitions APPROVED -> EXECUTING."""
    validate_debrief_state_transition(DebriefProposalStatus.APPROVED, DebriefProposalStatus.EXECUTING)


@pytest.mark.asyncio
async def test_f61_transition_executing_to_completed_all_success(briefing_service, test_user_id):
    """F-61: All operations succeed transitions EXECUTING -> COMPLETED."""
    validate_debrief_state_transition(DebriefProposalStatus.EXECUTING, DebriefProposalStatus.COMPLETED)


@pytest.mark.asyncio
async def test_f62_transition_executing_to_failed_op1_fail(briefing_service, test_user_id):
    """F-62: Operation 1 failure transitions EXECUTING -> FAILED with zero mutations."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[{"exact_title": "Failing Task 1", "exact_duration_minutes": 30}],
    )
    # Mock task creation failure on Op 1 during dispatch
    async def mock_fail(*args, **kwargs):
        raise RuntimeError("Database error on task creation")
    briefing_service.task_manager.create_task = mock_fail

    res = await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=True)
    assert res.status == DebriefProposalStatus.FAILED
    assert res.failure_reason == DebriefFailureReason.OPERATION_FAILED
    assert res.ordered_operations[0].status == DebriefOperationStatus.FAILED
    # Zero tasks created
    tasks = await briefing_service.task_manager.list_tasks(user_id=test_user_id)
    assert len(tasks) == 0


@pytest.mark.asyncio
async def test_f63_transition_executing_to_partially_executed_op_k_fail(briefing_service, test_user_id):
    """F-63: Operation K > 1 failure transitions EXECUTING -> PARTIALLY_EXECUTED."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[
            {"exact_title": "Good Task 1", "exact_duration_minutes": 30},
            {"exact_title": "Failing Task 2", "exact_duration_minutes": 30},
            {"exact_title": "Never Reached Task 3", "exact_duration_minutes": 30},
        ],
    )
    orig_create = briefing_service.task_manager.create_task
    call_count = 0
    async def mock_selective_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("Task 2 creation failed")
        return await orig_create(*args, **kwargs)
    briefing_service.task_manager.create_task = mock_selective_create

    res = await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=True)
    assert res.status == DebriefProposalStatus.PARTIALLY_EXECUTED
    assert res.ordered_operations[0].status == DebriefOperationStatus.COMPLETED
    assert res.ordered_operations[1].status == DebriefOperationStatus.FAILED
    assert res.ordered_operations[2].status == DebriefOperationStatus.SKIPPED

    # Op 1 remained committed
    tasks = await briefing_service.task_manager.list_tasks(user_id=test_user_id)
    assert len(tasks) == 1
    assert tasks[0].title == "Good Task 1"


@pytest.mark.asyncio
async def test_f64_authoritative_30m_ttl_and_draft_expiration(briefing_service, test_user_id):
    """F-64: Proves single authoritative 30m TTL contract and DRAFT expiration behavior."""
    # 1. Authoritative constant contract
    assert DEBRIEF_PROPOSAL_TTL_MINUTES == 30
    assert DEBRIEF_PROPOSAL_TTL_SECONDS == 1800

    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        raw_notes="Debrief notes",
        task_candidates=[{"exact_title": "Follow up task"}],
    )
    # Check exact 30m TTL bound at creation
    expected_expiry = proposal.created_at + timedelta(minutes=DEBRIEF_PROPOSAL_TTL_MINUTES)
    assert abs((proposal.expires_at - expected_expiry).total_seconds()) < 1.0

    # 2. Remains valid at 29 minutes (< 30m)
    proposal.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    retrieved = await briefing_service.get_proposal(proposal.proposal_id, user_id=test_user_id)
    assert retrieved.status == DebriefProposalStatus.DRAFT

    # 3. Expires at 31 minutes (> 30m)
    proposal.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    retrieved_expired = await briefing_service.get_proposal(proposal.proposal_id, user_id=test_user_id)
    assert retrieved_expired.status == DebriefProposalStatus.EXPIRED

    # 4. Attempt to execute expired DRAFT fails closed with zero mutations
    proposal_2 = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[{"exact_title": "Expired task"}],
    )
    proposal_2.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(ValueError, match="expired"):
        await briefing_service.execute_proposal(proposal_2.proposal_id, user_id=test_user_id, approved=True)
    assert proposal_2.status == DebriefProposalStatus.EXPIRED
    assert proposal_2.failure_reason == DebriefFailureReason.EXPIRED

    # Zero tasks created
    tasks = await briefing_service.task_manager.list_tasks(user_id=test_user_id)
    assert not any(t.title == "Expired task" for t in tasks)


@pytest.mark.asyncio
async def test_f65_approved_proposal_30m_ttl_and_expiration(briefing_service, test_user_id):
    """F-65: Proves APPROVED proposal remains valid before 30m and expires after 30m with zero mutations."""
    # 1. APPROVED proposal valid before 30m executes successfully
    p_valid = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[{"exact_title": "Valid approved task"}],
    )
    p_valid.status = DebriefProposalStatus.APPROVED
    p_valid.expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    res_valid = await briefing_service.execute_proposal(p_valid.proposal_id, user_id=test_user_id, approved=True)
    assert res_valid.status == DebriefProposalStatus.COMPLETED

    # 2. APPROVED proposal expires after 30m
    p_expired = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[{"exact_title": "Expired approved task"}],
    )
    p_expired.status = DebriefProposalStatus.APPROVED
    p_expired.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    with pytest.raises(ValueError, match="expired"):
        await briefing_service.execute_proposal(p_expired.proposal_id, user_id=test_user_id, approved=True)

    assert p_expired.status == DebriefProposalStatus.EXPIRED
    assert p_expired.failure_reason == DebriefFailureReason.EXPIRED

    # Zero mutations committed for expired proposal
    tasks = await briefing_service.task_manager.list_tasks(user_id=test_user_id)
    assert not any(t.title == "Expired approved task" for t in tasks)


def test_f66_illegal_terminal_state_transitions_rejected():
    """F-66: Transitions out of terminal states raise DebriefStateTransitionError."""
    terminal_states = [
        DebriefProposalStatus.COMPLETED,
        DebriefProposalStatus.FAILED,
        DebriefProposalStatus.PARTIALLY_EXECUTED,
        DebriefProposalStatus.ABORTED,
        DebriefProposalStatus.EXPIRED,
    ]
    for term in terminal_states:
        with pytest.raises(DebriefStateTransitionError):
            validate_debrief_state_transition(term, DebriefProposalStatus.APPROVED)
        with pytest.raises(DebriefStateTransitionError):
            validate_debrief_state_transition(term, DebriefProposalStatus.EXECUTING)


def test_f67_illegal_skipping_transitions_rejected():
    """F-67: Skipping mandatory states raises DebriefStateTransitionError."""
    # Cannot jump DRAFT -> EXECUTING without APPROVED
    with pytest.raises(DebriefStateTransitionError):
        validate_debrief_state_transition(DebriefProposalStatus.DRAFT, DebriefProposalStatus.EXECUTING)
    # Cannot jump EXECUTING -> ABORTED
    with pytest.raises(DebriefStateTransitionError):
        validate_debrief_state_transition(DebriefProposalStatus.EXECUTING, DebriefProposalStatus.ABORTED)


@pytest.mark.asyncio
async def test_f68_transition_approved_to_failed_on_tier_1_preflight_failure(briefing_service, test_user_id):
    """F-68: Tier 1 preflight failure transitions APPROVED -> FAILED with zero mutations."""
    # Seed a commitment and reference it in proposal
    c = await briefing_service.commitment_manager.create_commitment(
        title="Q4 Architecture",
        user_id=test_user_id,
        target_date=datetime.now(timezone.utc) + timedelta(days=7),
    )
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[{"exact_title": "Draft spec", "exact_commitment_id": c.id}],
    )

    # Concurrently mutate commitment status to break state hash
    await briefing_service.commitment_manager.complete_commitment(commitment_id=c.id, user_id=test_user_id)

    with pytest.raises(ValueError, match="state has changed concurrently"):
        await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=True)

    # Status must be FAILED, not EXECUTING
    assert proposal.status == DebriefProposalStatus.FAILED
    assert proposal.failure_reason == DebriefFailureReason.STATE_CHANGED
    # Zero tasks created
    tasks = await briefing_service.task_manager.list_tasks(user_id=test_user_id)
    assert len(tasks) == 0


# ==============================================================================
# DURATION CONTRACT & MEMORY LIFECYCLE TESTS (F-69 through F-72)
# ==============================================================================

def test_f69_exact_duration_minutes_boundary_validation():
    """F-69: Duration boundary validation: 14 rejected, 15 accepted, 1440 accepted, 1441 rejected."""
    # 14 is rejected
    with pytest.raises(ValidationError):
        TaskCreateExecutionPrecondition(exact_title="Short task", exact_duration_minutes=14)

    # 15 is accepted
    p15 = TaskCreateExecutionPrecondition(exact_title="15 min task", exact_duration_minutes=15)
    assert p15.exact_duration_minutes == 15

    # 1440 is accepted
    p1440 = TaskCreateExecutionPrecondition(exact_title="All day task", exact_duration_minutes=1440)
    assert p1440.exact_duration_minutes == 1440

    # 1441 is rejected
    with pytest.raises(ValidationError):
        TaskCreateExecutionPrecondition(exact_title="Too long task", exact_duration_minutes=1441)


@pytest.mark.asyncio
async def test_f70_absent_duration_follows_phase_6e_default_binding(briefing_service, test_user_id):
    """F-70: Absent duration resolves to Phase 6E default (30m) and binds into proposal & task metadata."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        task_candidates=[{"exact_title": "Task without duration"}],
    )
    tc = proposal.ordered_operations[0].precondition.task_create
    assert tc.exact_duration_minutes == 30  # Bound at proposal creation

    res = await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=True)
    task_id = res.ordered_operations[0].created_entity_id
    task = await briefing_service.task_manager.get_task(task_id, user_id=test_user_id)
    assert task.metadata["duration_minutes"] == 30  # Immutable bound effective duration


@pytest.mark.asyncio
async def test_f71_user_confirmed_debrief_memory_lifecycle_provenance(briefing_service, test_user_id):
    """F-71: User-confirmed debrief memory is created with USER_EXPLICIT provenance and ACTIVE status."""
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        memory_candidates=[{"exact_content": "Key decision: Launch date set to Nov 1"}],
    )
    res = await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=True)
    mem_id = res.ordered_operations[0].created_entity_id

    memory = await briefing_service.memory_manager.store.get(mem_id)
    assert memory is not None
    assert memory.source == MemorySource.USER_EXPLICIT
    assert memory.status == MemoryStatus.ACTIVE
    assert memory.metadata["provenance"] == "event_debrief"


def test_f72_fabricated_memory_source_rejected_or_server_overridden():
    """F-72: Precondition rejects injected source fields via extra='forbid'."""
    with pytest.raises(ValidationError):
        MemoryCreateExecutionPrecondition(
            exact_content="Sneaky memory",
            source_event_id="evt_123",
            source="inferred",  # Injected extra field
        )
    with pytest.raises(ValidationError):
        MemoryCreateExecutionPrecondition(
            exact_content="Sneaky memory",
            source_event_id="evt_123",
            exact_source="system",  # Injected extra field
        )


def test_f72_b_preconditions_exact_schema_verification():
    """F-72b: Execution preconditions strictly enforce approved exact_* schema and forbid extra fields."""
    now_utc = datetime.now(timezone.utc)

    # 1. TaskCreateExecutionPrecondition: exact_title, exact_due_at, exact_duration_minutes, exact_priority, exact_commitment_id
    tc = TaskCreateExecutionPrecondition(
        exact_title="Deliver design system",
        exact_due_at=now_utc,
        exact_duration_minutes=45,
        exact_priority=TaskPriority.HIGH,
        exact_commitment_id="cmt_123",
    )
    assert tc.exact_title == "Deliver design system"
    assert tc.exact_due_at == now_utc
    assert tc.exact_duration_minutes == 45
    assert tc.exact_priority == TaskPriority.HIGH
    assert tc.exact_commitment_id == "cmt_123"

    with pytest.raises(ValidationError):
        TaskCreateExecutionPrecondition(exact_title="Invalid extra", extra_field="bad")

    # 2. ReminderCreateExecutionPrecondition: exact_title, exact_scheduled_at, linked_entity_type, linked_entity_id
    rc = ReminderCreateExecutionPrecondition(
        exact_title="Standup reminder",
        exact_scheduled_at=now_utc,
        linked_entity_type=ReminderLinkType.TASK,
        linked_entity_id="task_456",
    )
    assert rc.exact_title == "Standup reminder"
    assert rc.exact_scheduled_at == now_utc
    assert rc.linked_entity_type == ReminderLinkType.TASK
    assert rc.linked_entity_id == "task_456"

    with pytest.raises(ValidationError):
        ReminderCreateExecutionPrecondition(exact_title="Bad extra", exact_scheduled_at=now_utc, extra_prop=1)

    # 3. MemoryCreateExecutionPrecondition: exact_content, exact_memory_type, exact_confidence, source_event_id, context_tags
    mc = MemoryCreateExecutionPrecondition(
        exact_content="Decision: use Postgres",
        exact_memory_type=MemoryType.EPISODIC,
        exact_confidence=0.95,
        source_event_id="evt_sync_123",
        context_tags=["architecture", "decision"],
    )
    assert mc.exact_content == "Decision: use Postgres"
    assert mc.exact_memory_type == MemoryType.EPISODIC
    assert mc.exact_confidence == 0.95
    assert mc.source_event_id == "evt_sync_123"
    assert mc.context_tags == ["architecture", "decision"]

    with pytest.raises(ValidationError):
        MemoryCreateExecutionPrecondition(exact_content="Bad", source_event_id="evt_1", extra_attr="forbid")


@pytest.mark.asyncio
async def test_f73_memory_execution_independent_of_permission_type(briefing_service, test_user_id):
    """F-73: Proves memory creation does NOT require or depend on PermissionType.MEMORY."""
    # 1. Verify PermissionType does not define MEMORY
    assert "MEMORY" not in PermissionType.__members__
    assert not any(p.value == "memory" for p in PermissionType)

    # 2. Deny ALL system permissions in PermissionManager
    for p_type in PermissionType:
        await briefing_service.permission_manager.update_permission(
            permission_type=p_type,
            status=PermissionStatus.DENIED,
            user_id=test_user_id,
        )

    # 3. Propose and execute a debrief proposal with memory ONLY
    proposal = await briefing_service.propose_debrief(
        event_id="evt_sync_123",
        user_id=test_user_id,
        memory_candidates=[{"exact_content": "Confirmed decision without permissions"}],
    )
    # Execution must succeed because memory creation does NOT depend on a nonexistent PermissionType.MEMORY
    res = await briefing_service.execute_proposal(proposal.proposal_id, user_id=test_user_id, approved=True)
    assert res.status == DebriefProposalStatus.COMPLETED
    assert res.ordered_operations[0].status == DebriefOperationStatus.COMPLETED

    mem_id = res.ordered_operations[0].created_entity_id
    memory = await briefing_service.memory_manager.store.get(mem_id)
    assert memory is not None
    assert memory.source == MemorySource.USER_EXPLICIT
    assert memory.content == "Confirmed decision without permissions"


# ==============================================================================
# TOOL REGISTRY INTEGRATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_briefing_tools_registered_and_governed(briefing_service, test_user_id):
    """Verifies GenerateEventBriefingTool, ProposeEventDebriefTool, and ApplyEventDebriefTool."""
    gen_tool = GenerateEventBriefingTool(briefing_service=briefing_service)
    assert gen_tool.definition.risk_level.value == "low"
    assert gen_tool.definition.requires_confirmation is False

    briefing_res = await gen_tool.execute({"event_id": "evt_sync_123", "_user_id": test_user_id})
    assert briefing_res["status"] == "success"

    prop_tool = ProposeEventDebriefTool(briefing_service=briefing_service)
    assert prop_tool.definition.risk_level.value == "low"
    assert prop_tool.definition.requires_confirmation is False

    prop_res = await prop_tool.execute({
        "event_id": "evt_sync_123",
        "_user_id": test_user_id,
        "raw_notes": "Tool test notes",
    })
    assert prop_res["status"] == "success"
    proposal_id = prop_res["proposal_id"]

    apply_tool = ApplyEventDebriefTool(briefing_service=briefing_service)
    assert apply_tool.definition.risk_level.value == "high"
    assert apply_tool.definition.requires_confirmation is True

    apply_res = await apply_tool.execute({
        "proposal_id": proposal_id,
        "_user_id": test_user_id,
    })
    assert apply_res["status"] == "success"
    assert apply_res["proposal_status"] == "completed"
