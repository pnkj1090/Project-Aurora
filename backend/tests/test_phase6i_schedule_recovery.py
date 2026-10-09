import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
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
    InMemoryCommitmentStore,
)
from app.services.identity.models import DEFAULT_LOCAL_USER_ID, DeviceIdentity, UserIdentity
from app.services.permissions.manager import InMemoryPermissionStore, PermissionManager
from app.services.permissions.models import PermissionStatus, PermissionType
from app.services.reminders import InMemoryReminderStore, Reminder, ReminderManager
from app.services.tasks import InMemoryTaskStore, Task, TaskManager, TaskPriority, TaskStatus

from app.services.agenda.service import AgendaService
from app.services.agenda.schedule_recovery import (
    Phase6IWorkingHoursDependencyResolver,
    ProposalLifecycleCoordinator,
    ProposalStateLockManager,
    ScheduleRecoveryExecutionStore,
    ScheduleRecoveryProposalBuilder,
    UserExecutionLockManager,
    get_recovery_coordinator_dependency,
    get_schedule_recovery_coordinator,
)
from app.services.agenda.schedule_recovery_models import (
    ActiveExecutionLockConflictError,
    ApplyRecoveryProposalRequest,
    AuthorizationPrecedenceError,
    CalendarExclusivityPrecondition,
    CancelRecoveryProposalRequest,
    CommitmentExternalDependency,
    EphemeralRecoveryNote,
    EvaluateRecoveryProposalRequest,
    ExecutionResultCapacityExceededError,
    PartialExecutionError,
    Phase6IError,
    PreconditionFailedError,
    PreconditionMismatch,
    ProposalAlreadyTerminalError,
    ProposalCapacityExceededError,
    ProposalExpiredError,
    ProposalNotFoundError,
    ProposalStateConflictError,
    ProposeScheduleRecoveryRequest,
    RecoveryEvaluationResult,
    RecoveryExecutionDiagnostics,
    RecoveryExecutionResult,
    RecoveryExecutionStatus,
    RecoveryOperationPayload,
    RecoveryOperationType,
    RecoveryProposalStatus,
    RecoveryReminderPrecondition,
    RecoveryTaskPrecondition,
    RescheduleReminderPayload,
    RescheduleTaskPayload,
    ResultNotFoundError,
    ScheduleRecoveryOperation,
    ScheduleRecoveryProposal,
    WorkingHoursExternalDependency,
)
from app.services.tools.builtins.schedule_recovery_tool import (
    ApplyScheduleRecoveryProposalTool,
    CancelScheduleRecoveryProposalTool,
    EvaluateScheduleRecoveryTool,
    GetScheduleRecoveryProposalTool,
    GetScheduleRecoveryResultTool,
    ProposeScheduleRecoveryTool,
)
from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal


# ==============================================================================
# Test Fixtures & Setup Helpers
# ==============================================================================

@pytest.fixture
def test_user_id() -> str:
    return f"usr_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def other_user_id() -> str:
    return f"usr_other_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def recovery_harness(test_user_id: str):
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

    wh_resolver = Phase6IWorkingHoursDependencyResolver(agenda_service=agenda_service)

    builder = ScheduleRecoveryProposalBuilder(
        task_manager=task_manager,
        reminder_manager=rem_manager,
        commitment_manager=cmt_manager,
        calendar_manager=cal_manager,
        working_hours_resolver=wh_resolver,
        permission_manager=perm_manager,
    )

    store = ScheduleRecoveryExecutionStore()
    state_locks = ProposalStateLockManager()
    user_locks = UserExecutionLockManager()

    coordinator = ProposalLifecycleCoordinator(
        store=store,
        state_lock_manager=state_locks,
        user_lock_manager=user_locks,
        working_hours_resolver=wh_resolver,
        builder=builder,
        task_manager=task_manager,
        reminder_manager=rem_manager,
        commitment_manager=cmt_manager,
        calendar_manager=cal_manager,
    )

    return {
        "user_id": test_user_id,
        "coordinator": coordinator,
        "builder": builder,
        "store": store,
        "wh_resolver": wh_resolver,
        "task_manager": task_manager,
        "rem_manager": rem_manager,
        "cmt_manager": cmt_manager,
        "cal_manager": cal_manager,
        "perm_manager": perm_manager,
        "agenda_service": agenda_service,
    }


# ==============================================================================
# 1. Invariant Tests: Preconditions, Enums, Payloads & Invariants (I-01 .. I-100)
# ==============================================================================

@pytest.mark.asyncio
async def test_invariants_task_and_reminder_preconditions(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    rem_mgr: ReminderManager = h["rem_manager"]

    # Create task & reminder
    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task A", user_id=user_id, due_at=now + timedelta(hours=3))
    r = await rem_mgr.create_reminder(title="Reminder A", user_id=user_id, scheduled_at=now + timedelta(hours=4))

    # Test I-01 .. I-10: Task precondition immutability & validation
    tp = RecoveryTaskPrecondition(
        task_id=t.id,
        expected_version=getattr(t, "version", 1),
        expected_status=t.status,
        expected_due_at=t.due_at,
        source_commitment_id=t.source_commitment_id,
        expected_state_hash=hashlib.sha256(b"state_hash_123456789012345678901234567890123456789012345678901234").hexdigest(),
    )
    assert tp.task_id == t.id
    assert tp.expected_version == 1

    # Test I-07: Frozen model raises on reassignment
    with pytest.raises(Exception):
        tp.task_id = "other_id"  # type: ignore

    # Test I-21 .. I-30: Reminder precondition immutability & validation
    rp = RecoveryReminderPrecondition(
        reminder_id=r.id,
        expected_scheduled_at=r.scheduled_at,
        expected_status=r.status,
        expected_state_hash=hashlib.sha256(b"reminder_hash_1234567890123456789012345678901234567890123456789012").hexdigest(),
    )
    assert rp.reminder_id == r.id
    with pytest.raises(Exception):
        rp.reminder_id = "other_id"  # type: ignore


@pytest.mark.asyncio
async def test_working_hours_fails_closed_zero_utc_fallback(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]

    # Mock agenda service that fails timezone resolution
    class BrokenAgendaService:
        async def resolve_effective_timezone(self, **kwargs):
            raise RuntimeError("Corrupted timezone provider")

        def _resolve_working_hours(self, *args):
            return (9, 18)

    broken_resolver = Phase6IWorkingHoursDependencyResolver(agenda_service=BrokenAgendaService())
    now = datetime.now(timezone.utc)

    # Must raise PreconditionFailedError with error_code "WORKING_HOURS_UNRESOLVABLE"
    with pytest.raises(PreconditionFailedError) as exc_info:
        await broken_resolver.resolve_dependency_async(
            user_id=user_id,
            client_preferred_timezone="Invalid/Nonexistent_Timezone",
            target_window_start=now,
            target_window_end=now + timedelta(days=2),
        )
    assert exc_info.value.error_code == "WORKING_HOURS_UNRESOLVABLE"


# ==============================================================================
# 2. Builder Authorization-Before-Read (13-Step Sequence)
# ==============================================================================

@pytest.mark.asyncio
async def test_builder_authorization_before_read_success(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    rem_mgr: ReminderManager = h["rem_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task 1", user_id=user_id, due_at=now + timedelta(hours=3))
    r = await rem_mgr.create_reminder(title="Reminder 1", user_id=user_id, scheduled_at=now + timedelta(hours=4))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        reminder_ids=[r.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
        user_intent="Resolve overload",
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)
    assert proposal.status == RecoveryProposalStatus.PENDING_CONFIRMATION
    assert len(proposal.operations) == 2
    assert proposal.user_id == user_id
    assert proposal.expires_at > proposal.created_at


@pytest.mark.asyncio
async def test_builder_authorization_fails_closed_when_unauthorized(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    perm_mgr: PermissionManager = h["perm_manager"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Secret", user_id=user_id, due_at=now + timedelta(hours=3))

    # Revoke TASKS permission
    await perm_mgr.update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=user_id)

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    with pytest.raises(AuthorizationPrecedenceError):
        await coordinator.propose_schedule_recovery(user_id=user_id, request=req)


# ==============================================================================
# 3. Lifecycle, Lazy TTL Expiration & Terminal Immutability
# ==============================================================================

@pytest.mark.asyncio
async def test_ttl_rule_valid_vs_expired(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]
    store: ScheduleRecoveryExecutionStore = h["store"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task TTL", user_id=user_id, due_at=now + timedelta(hours=3))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)

    # Valid when now_utc < expires_at
    p_fetched = await coordinator.get_proposal(proposal.proposal_id, authenticated_user_id=user_id)
    assert p_fetched.status == RecoveryProposalStatus.PENDING_CONFIRMATION

    # Manually backdate expires_at to simulate now_utc >= expires_at
    expired_proposal = ScheduleRecoveryProposal(
        proposal_id=proposal.proposal_id,
        user_id=proposal.user_id,
        created_at=proposal.created_at - timedelta(hours=25),
        expires_at=now - timedelta(seconds=1),  # Expired!
        status=RecoveryProposalStatus.PENDING_CONFIRMATION,
        operations=proposal.operations,
        task_preconditions=proposal.task_preconditions,
        reminder_preconditions=proposal.reminder_preconditions,
        commitment_dependencies=proposal.commitment_dependencies,
        working_hours_dependency=proposal.working_hours_dependency,
        calendar_precondition=proposal.calendar_precondition,
        preconditions_hash=proposal.preconditions_hash,
        ephemeral_notes=proposal.ephemeral_notes,
    )
    await store.store_proposal(expired_proposal)

    # get_proposal performs lazy expiration
    p_lazy = await coordinator.get_proposal(proposal.proposal_id, authenticated_user_id=user_id)
    assert p_lazy.status == RecoveryProposalStatus.EXPIRED


@pytest.mark.asyncio
async def test_terminal_proposal_immutability_and_evaluation_rejection(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Cancel", user_id=user_id, due_at=now + timedelta(hours=3))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)

    # Cancel proposal -> Transitions to CANCELLED (Terminal)
    cancelled = await coordinator.cancel_proposal(proposal.proposal_id, authenticated_user_id=user_id, reason="User decline")
    assert cancelled.status == RecoveryProposalStatus.CANCELLED

    # Re-cancelling raises ProposalAlreadyTerminalError
    with pytest.raises(ProposalAlreadyTerminalError):
        await coordinator.cancel_proposal(proposal.proposal_id, authenticated_user_id=user_id)

    # Evaluating a terminal proposal is strictly rejected with HTTP 409 error code
    with pytest.raises(Phase6IError) as exc_info:
        await coordinator.evaluate_proposal(proposal.proposal_id, authenticated_user_id=user_id)
    assert exc_info.value.error_code == "PROPOSAL_ALREADY_TERMINAL"


# ==============================================================================
# 4. Concurrency: Execution Locking (Second Apply Fails Closed, No IN_PROGRESS)
# ==============================================================================

@pytest.mark.asyncio
async def test_concurrent_apply_fails_closed_via_user_lock(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Concurrency", user_id=user_id, due_at=now + timedelta(hours=3))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)

    # Acquire user execution lock to simulate in-flight execution
    async with coordinator._user_lock_manager.acquire_execution_lock(user_id):
        # A second apply request must fail closed immediately
        with pytest.raises(ActiveExecutionLockConflictError):
            apply_req = ApplyRecoveryProposalRequest(confirmed=True)
            await coordinator.apply_proposal(proposal.proposal_id, authenticated_user_id=user_id, request=apply_req)


# ==============================================================================
# 5. Execution Pipeline: Cases A, B, C, D
# ==============================================================================

@pytest.mark.asyncio
async def test_case_d_full_success_http_200(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    rem_mgr: ReminderManager = h["rem_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Case D", user_id=user_id, due_at=now + timedelta(hours=3))
    r = await rem_mgr.create_reminder(title="Rem Case D", user_id=user_id, scheduled_at=now + timedelta(hours=4))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        reminder_ids=[r.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)

    # Dry-run evaluate first
    eval_res = await coordinator.evaluate_proposal(proposal.proposal_id, authenticated_user_id=user_id)
    assert eval_res.valid is True
    assert len(eval_res.mismatches) == 0

    # Apply proposal (Case D)
    apply_req = ApplyRecoveryProposalRequest(confirmed=True)
    res = await coordinator.apply_proposal(proposal.proposal_id, authenticated_user_id=user_id, request=apply_req)
    assert res.status == RecoveryExecutionStatus.EXECUTED
    assert len(res.completed_operations) == 2
    assert res.failed_operation_id is None

    # Proposal state is APPLIED
    p_applied = await coordinator.get_proposal(proposal.proposal_id, authenticated_user_id=user_id)
    assert p_applied.status == RecoveryProposalStatus.APPLIED
    assert p_applied.applied_at is not None


@pytest.mark.asyncio
async def test_case_a_preflight_failure_http_412(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Case A", user_id=user_id, due_at=now + timedelta(hours=3))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)

    # Induce drift: mutate task due_at externally before apply
    await task_mgr.update_task(task_id=t.id, user_id=user_id, due_at=now + timedelta(hours=10))

    # Apply proposal -> Preflight fails with PreconditionFailedError (Case A -> HTTP 412)
    with pytest.raises(PreconditionFailedError) as exc_info:
        apply_req = ApplyRecoveryProposalRequest(confirmed=True)
        await coordinator.apply_proposal(proposal.proposal_id, authenticated_user_id=user_id, request=apply_req)
    assert exc_info.value.error_code in ("TASK_VERSION_DRIFT", "TASK_STATE_HASH_MISMATCH")

    # Proposal becomes ABORTED
    p_aborted = await coordinator.get_proposal(proposal.proposal_id, authenticated_user_id=user_id)
    assert p_aborted.status == RecoveryProposalStatus.ABORTED


@pytest.mark.asyncio
async def test_case_b_operation_1_failure_http_409(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Case B", user_id=user_id, due_at=now + timedelta(hours=3))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)

    # Sabotage TaskManager.update_task to simulate mutation failure on Op 1
    async def failing_update_task(*args, **kwargs):
        raise RuntimeError("Disk I/O error during task update")

    task_mgr.update_task = failing_update_task

    # Apply proposal -> Op 1 fails -> Case B (PartialExecutionError with 0 completed -> HTTP 409)
    with pytest.raises(PartialExecutionError) as exc_info:
        apply_req = ApplyRecoveryProposalRequest(confirmed=True)
        await coordinator.apply_proposal(proposal.proposal_id, authenticated_user_id=user_id, request=apply_req)
    assert len(exc_info.value.completed_operations) == 0

    # Stored result status is FAILED
    res = await coordinator.get_execution_result(proposal.proposal_id, authenticated_user_id=user_id)
    assert res.status == RecoveryExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_case_c_operation_k_failure_http_409_no_rollback(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    rem_mgr: ReminderManager = h["rem_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Case C", user_id=user_id, due_at=now + timedelta(hours=3))
    r = await rem_mgr.create_reminder(title="Rem Case C", user_id=user_id, scheduled_at=now + timedelta(hours=4))

    req = ProposeScheduleRecoveryRequest(
        task_ids=[t.id],
        reminder_ids=[r.id],
        target_window_start=now + timedelta(hours=1),
        target_window_end=now + timedelta(days=2),
    )
    proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=req)

    # Op 1 is Task update, Op 2 is Reminder update. Sabotage ReminderManager to fail Op 2.
    async def failing_update_reminder(*args, **kwargs):
        raise RuntimeError("Notification subsystem unavailable")

    rem_mgr.update_reminder = failing_update_reminder

    # Apply proposal -> Op 2 fails -> Case C (PartialExecutionError with 1 completed, NO ROLLBACK -> HTTP 409)
    with pytest.raises(PartialExecutionError) as exc_info:
        apply_req = ApplyRecoveryProposalRequest(confirmed=True)
        await coordinator.apply_proposal(proposal.proposal_id, authenticated_user_id=user_id, request=apply_req)
    assert len(exc_info.value.completed_operations) == 1

    # Stored result status is PARTIALLY_EXECUTED
    res = await coordinator.get_execution_result(proposal.proposal_id, authenticated_user_id=user_id)
    assert res.status == RecoveryExecutionStatus.PARTIALLY_EXECUTED


# ==============================================================================
# 6. Result Store: Timestamp-Priority Eviction Semantics
# ==============================================================================

@pytest.mark.asyncio
async def test_result_store_timestamp_priority_eviction():
    store = ScheduleRecoveryExecutionStore()
    now = datetime.now(timezone.utc)

    # Store 10,000 results
    store.MAX_RESULTS = 5  # Lower limit for unit test efficiency
    for i in range(5):
        diagnostics = RecoveryExecutionDiagnostics(
            preflight_checked_at=now,
            preflight_duration_ms=1.0,
            execution_duration_ms=2.0,
            total_duration_ms=3.0,
        )
        res = RecoveryExecutionResult(
            execution_id=f"exec_{i}",
            proposal_id=f"prop_{i}",
            user_id="user_1",
            status=RecoveryExecutionStatus.EXECUTED,
            started_at=now,
            completed_at=now + timedelta(seconds=i),
            completed_operations=(f"op_{i}",),
            diagnostics=diagnostics,
        )
        await store.store_result(res)

    # Result 6 triggers eviction of oldest terminal result
    res6 = RecoveryExecutionResult(
        execution_id="exec_5",
        proposal_id="prop_5",
        user_id="user_1",
        status=RecoveryExecutionStatus.EXECUTED,
        started_at=now,
        completed_at=now + timedelta(seconds=10),
        completed_operations=("op_5",),
        diagnostics=diagnostics,
    )
    await store.store_result(res6)
    # Oldest (prop_0) was evicted
    assert await store.get_result("prop_0") is None
    # Newest (prop_5) is present
    assert await store.get_result("prop_5") is not None


# ==============================================================================
# 7. Semantic-Parity Builtin Tools (All 6 Tools)
# ==============================================================================

@pytest.mark.asyncio
async def test_semantic_parity_tools(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task Tool", user_id=user_id, due_at=now + timedelta(hours=3))

    # Tool 1: propose_schedule_recovery
    t_propose = ProposeScheduleRecoveryTool(coordinator=coordinator)
    prop_dict = await t_propose.execute(user_id=user_id, task_ids=[t.id])
    prop_id = prop_dict["proposal_id"]
    assert prop_dict["status"] == "PENDING_CONFIRMATION"

    # Tool 2: get_schedule_recovery_proposal
    t_get = GetScheduleRecoveryProposalTool(coordinator=coordinator)
    get_dict = await t_get.execute(user_id=user_id, proposal_id=prop_id)
    assert get_dict["proposal_id"] == prop_id

    # Tool 6: evaluate_schedule_recovery
    t_eval = EvaluateScheduleRecoveryTool(coordinator=coordinator)
    eval_dict = await t_eval.execute(user_id=user_id, proposal_id=prop_id)
    assert eval_dict["valid"] is True

    # Tool 4: apply_schedule_recovery_proposal
    t_apply = ApplyScheduleRecoveryProposalTool(coordinator=coordinator)
    apply_dict = await t_apply.execute(user_id=user_id, proposal_id=prop_id, confirmed=True)
    assert apply_dict["status"] == "EXECUTED"

    # Tool 5: get_schedule_recovery_result
    t_res = GetScheduleRecoveryResultTool(coordinator=coordinator)
    res_dict = await t_res.execute(user_id=user_id, proposal_id=prop_id)
    assert res_dict["status"] == "EXECUTED"

    # Tool 3: cancel_schedule_recovery_proposal (on new proposal)
    prop2_dict = await t_propose.execute(user_id=user_id, task_ids=[t.id])
    t_cancel = CancelScheduleRecoveryProposalTool(coordinator=coordinator)
    cancel_dict = await t_cancel.execute(user_id=user_id, proposal_id=prop2_dict["proposal_id"])
    assert cancel_dict["status"] == "CANCELLED"


# ==============================================================================
# 8. REST API Integration: All 6 Endpoints
# ==============================================================================

@pytest.mark.asyncio
async def test_rest_api_all_six_endpoints(recovery_harness):
    h = recovery_harness
    user_id = h["user_id"]
    task_mgr: TaskManager = h["task_manager"]
    coordinator: ProposalLifecycleCoordinator = h["coordinator"]

    now = datetime.now(timezone.utc)
    t = await task_mgr.create_task(title="Task REST", user_id=user_id, due_at=now + timedelta(hours=3))

    # Override FastAPI dependency
    app.dependency_overrides[get_recovery_coordinator_dependency] = lambda: coordinator
    app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        user=UserIdentity(user_id=user_id),
        device=DeviceIdentity(device_id="dev_test", user_id=user_id)
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. POST /api/v1/schedule/recovery/propose -> 201 Created
        payload = {
            "task_ids": [t.id],
            "target_window_start": (now + timedelta(hours=1)).isoformat(),
            "target_window_end": (now + timedelta(days=2)).isoformat(),
            "user_intent": "REST Test Overload",
        }
        resp = await ac.post("/api/v1/schedule/recovery/propose", json=payload)
        assert resp.status_code == 201
        p_data = resp.json()
        proposal_id = p_data["proposal_id"]
        assert p_data["status"] == "PENDING_CONFIRMATION"

        # 2. GET /api/v1/schedule/recovery/proposals/{proposal_id} -> 200 OK
        resp = await ac.get(f"/api/v1/schedule/recovery/proposals/{proposal_id}")
        assert resp.status_code == 200
        assert resp.json()["proposal_id"] == proposal_id

        # 6. POST /api/v1/schedule/recovery/evaluate -> 200 OK
        resp = await ac.post("/api/v1/schedule/recovery/evaluate", json={"proposal_id": proposal_id})
        assert resp.status_code == 200
        assert resp.json()["valid"] is True

        # 4. POST /api/v1/schedule/recovery/proposals/{proposal_id}/apply -> 200 OK (Case D)
        resp = await ac.post(f"/api/v1/schedule/recovery/proposals/{proposal_id}/apply", json={"confirmed": True})
        assert resp.status_code == 200
        assert resp.json()["status"] == "EXECUTED"

        # 5. GET /api/v1/schedule/recovery/proposals/{proposal_id}/result -> 200 OK
        resp = await ac.get(f"/api/v1/schedule/recovery/proposals/{proposal_id}/result")
        assert resp.status_code == 200
        assert resp.json()["status"] == "EXECUTED"

        # 3. POST /api/v1/schedule/recovery/proposals/{proposal_id}/cancel -> 200 OK (on second proposal)
        resp2 = await ac.post("/api/v1/schedule/recovery/propose", json=payload)
        prop2_id = resp2.json()["proposal_id"]
        resp_cancel = await ac.post(f"/api/v1/schedule/recovery/proposals/{prop2_id}/cancel", json={"reason": "REST cancel"})
        assert resp_cancel.status_code == 200
        assert resp_cancel.json()["status"] == "CANCELLED"

    app.dependency_overrides.clear()
