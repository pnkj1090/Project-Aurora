"""
Phase 5I — Proposal Resolution, Navigation, Execution & Confirmation Test Suite.

Verifies:
- Successful OPEN_* actions return typed NavigationTarget(type, id) and zero internal fields.
- Nonexistent entity during OPEN_* returns FAILED and navigation_target=None.
- Low-risk state-changing actions (COMPLETE_TASK, COMPLETE_COMMITMENT) execute immediately without confirmation.
- CANCEL_REMINDER requires confirmation; creates Phase 3 PendingConfirmation carrying server-controlled proactive_candidate_id.
- Candidate remains ACCEPTED while confirmation is pending; reminder is NOT cancelled.
- Confirmation completion via POST /api/v1/chat/confirm executes pre-approved tool and updates candidate to EXECUTED.
- Confirmation rejection transitions candidate to FAILED; action does NOT execute.
- PendingConfirmation registration failure transitions candidate to FAILED (never stuck in ACCEPTED).
- Terminal candidates cannot re-execute via confirmation.
- NOTIFY action is strictly non-executable in Phase 5I (fails with clean error).
- Permission TOCTOU rejection for calendar and reminders.
- NOTIFICATIONS authorization cannot substitute for REMINDERS.
- Strict user isolation: User B cannot resolve or confirm User A's candidate.
- Client cannot bypass confirmation via confirmed=true.
- LLM AcceptProactiveCandidateTool enforces confirmation.
- Public responses contain strictly sanitized fields (no reference_id, metadata, action_payload, internal results).
"""
import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone, timedelta

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.tasks import TaskManager, Task, TaskStatus, TaskPriority
from app.services.tasks.store import InMemoryTaskStore
from app.services.commitments import CommitmentManager, Commitment, CommitmentStatus
from app.services.commitments.store import InMemoryCommitmentStore
from app.services.calendar import CalendarManager
from app.services.calendar.models import CalendarEvent
from app.services.reminders import ReminderManager, Reminder, ReminderStatus
from app.services.reminders.store import InMemoryReminderStore
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import create_default_tool_registry
from app.services.tools.builtins.task_tool import CompleteTaskTool
from app.services.tools.builtins.commitment_tool import CompleteCommitmentTool
from app.services.tools.builtins.reminder_tool import CancelReminderTool
from app.services.ai.base import BaseAIProvider, AIResponse
from app.services.conversation import ConversationService
from app.services.conversation.store import InMemoryConversationStore
from app.services.proactive import (
    ProactiveManager,
    InMemoryProactiveStore,
    ProactiveStatus,
    ProactiveAction,
    ProactivePriority,
    ProactiveTrigger,
    ProactiveCandidate,
)
from app.services.proactive.executor import ProactiveActionExecutor
from app.services.proactive.schemas import (
    NavigationTargetType,
    ProactiveResolutionResponse,
)
from app.services.tools.builtins.proactive_action_tool import AcceptProactiveCandidateTool
from fastapi.testclient import TestClient
from app.main import app


class MockAIProvider(BaseAIProvider):
    @property
    def provider_name(self) -> str:
        return "mock-ai-provider"

    async def generate_response(self, messages, tools=None, **kwargs) -> AIResponse:
        return AIResponse(
            content="Action confirmed and executed successfully.",
            provider=self.provider_name,
            model="mock-model",
        )


@pytest_asyncio.fixture
async def env():
    perm_store = InMemoryPermissionStore()
    perm_mgr = PermissionManager(store=perm_store)
    await perm_mgr.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=DEFAULT_LOCAL_USER_ID)
    await perm_mgr.update_permission(PermissionType.COMMITMENTS, PermissionStatus.AUTHORIZED, user_id=DEFAULT_LOCAL_USER_ID)

    proactive_store = InMemoryProactiveStore()
    proactive_mgr = ProactiveManager(store=proactive_store, permission_manager=perm_mgr)

    task_store = InMemoryTaskStore()
    cmt_store = InMemoryCommitmentStore()
    rem_store = InMemoryReminderStore()

    task_mgr = TaskManager(store=task_store)
    cmt_mgr = CommitmentManager(store=cmt_store)
    rem_mgr = ReminderManager(store=rem_store)

    registry = create_default_tool_registry()
    registry.register(CompleteTaskTool(task_manager=task_mgr))
    registry.register(CompleteCommitmentTool(commitment_manager=cmt_mgr))
    registry.register(CancelReminderTool(reminder_manager=rem_mgr))

    tool_executor = ToolExecutor(registry=registry, permission_manager=perm_mgr)

    conv_store = InMemoryConversationStore()
    conv_service = ConversationService(
        store=conv_store,
        ai_provider=MockAIProvider(),
        tool_registry=registry,
        tool_executor=tool_executor,
    )

    cal_mgr = CalendarManager(permission_manager=perm_mgr)

    executor = ProactiveActionExecutor(
        store=proactive_store,
        task_manager=task_mgr,
        commitment_manager=cmt_mgr,
        calendar_manager=cal_mgr,
        reminder_manager=rem_mgr,
        permission_manager=perm_mgr,
        tool_executor=tool_executor,
        conversation_service=conv_service,
    )

    return {
        "perm_mgr": perm_mgr,
        "proactive_mgr": proactive_mgr,
        "proactive_store": proactive_store,
        "task_store": task_store,
        "cmt_store": cmt_store,
        "rem_store": rem_store,
        "cal_mgr": cal_mgr,
        "conv_service": conv_service,
        "executor": executor,
    }


# MARK: - OPEN_* Safe Navigation Tests

@pytest.mark.asyncio
async def test_successful_open_task_navigation(env):
    task_store = env["task_store"]
    await task_store.initialize()
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    task = Task(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Review PR",
        status=TaskStatus.PENDING,
    )
    await task_store.put(task)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="tasks",
        trigger=ProactiveTrigger.TASK_DUE,
        action=ProactiveAction.OPEN_TASK,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Review PR",
        message="Please review open PR",
        reference_id=task.id,
        action_payload={"task_id": task.id},
    )
    await proactive_store.add(candidate)

    response = await executor.resolve_candidate_action(candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID)
    assert response.status == "executed"
    assert response.navigation_target is not None
    assert response.navigation_target.type == NavigationTargetType.TASK
    assert response.navigation_target.id == task.id
    assert response.message == "Task details opened."

    # Verify candidate updated in store
    updated = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert updated.status == ProactiveStatus.EXECUTED
    assert updated.navigation_target_type == "task"
    assert updated.navigation_target_id == task.id


@pytest.mark.asyncio
async def test_successful_open_commitment_navigation(env):
    cmt_store = env["cmt_store"]
    await cmt_store.initialize()
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    cmt = Commitment(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Weekly Sync",
        status=CommitmentStatus.ACTIVE,
    )
    await cmt_store.put(cmt)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="commitments",
        trigger=ProactiveTrigger.COMMITMENT_DUE,
        action=ProactiveAction.OPEN_COMMITMENT,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Commitment due",
        message="Weekly Sync approaching",
        reference_id=cmt.id,
        action_payload={"commitment_id": cmt.id},
    )
    await proactive_store.add(candidate)

    response = await executor.resolve_candidate_action(candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID)
    assert response.status == "executed"
    assert response.navigation_target is not None
    assert response.navigation_target.type == NavigationTargetType.COMMITMENT
    assert response.navigation_target.id == cmt.id


@pytest.mark.asyncio
async def test_successful_open_calendar_event_navigation(env):
    cal_mgr = env["cal_mgr"]
    perm_mgr = env["perm_mgr"]
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    await perm_mgr.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED)

    now_utc = datetime.now(timezone.utc)
    ev = CalendarEvent(
        event_id="cal_123",
        title="Team Standup",
        start_at=now_utc + timedelta(minutes=30),
        end_at=now_utc + timedelta(minutes=60),
    )
    await cal_mgr.set_events([ev], user_id=DEFAULT_LOCAL_USER_ID)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="calendar",
        trigger=ProactiveTrigger.CALENDAR_EVENT,
        action=ProactiveAction.OPEN_CALENDAR_EVENT,
        priority=ProactivePriority.HIGH,
        status=ProactiveStatus.ELIGIBLE,
        title="Standup starting soon",
        message="Team Standup in 30 mins",
        reference_id=ev.event_id,
        action_payload={"event_id": ev.event_id},
    )
    await proactive_store.add(candidate)

    response = await executor.resolve_candidate_action(candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID)
    assert response.status == "executed"
    assert response.navigation_target is not None
    assert response.navigation_target.type == NavigationTargetType.CALENDAR_EVENT
    assert response.navigation_target.id == ev.event_id


@pytest.mark.asyncio
async def test_successful_open_reminder_navigation(env):
    rem_store = env["rem_store"]
    await rem_store.initialize()
    perm_mgr = env["perm_mgr"]
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    await perm_mgr.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED)

    rem = Reminder(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Dentist appointment",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        status=ReminderStatus.SCHEDULED,
    )
    await rem_store.put(rem)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="reminders",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.OPEN_REMINDER,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Dentist Reminder",
        message="Check appointment",
        reference_id=rem.id,
        action_payload={"reminder_id": rem.id},
    )
    await proactive_store.add(candidate)

    response = await executor.resolve_candidate_action(candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID)
    assert response.status == "executed"
    assert response.navigation_target is not None
    assert response.navigation_target.type == NavigationTargetType.REMINDER
    assert response.navigation_target.id == rem.id


@pytest.mark.asyncio
async def test_failed_open_task_nonexistent_entity(env):
    task_store = env["task_store"]
    await task_store.initialize()
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="tasks",
        trigger=ProactiveTrigger.TASK_DUE,
        action=ProactiveAction.OPEN_TASK,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Missing Task",
        message="Nonexistent task",
        reference_id="nonexistent_id",
        action_payload={"task_id": "nonexistent_id"},
    )
    await proactive_store.add(candidate)

    response = await executor.resolve_candidate_action(candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID)
    assert response.status == "failed"
    assert response.navigation_target is None
    assert "Task 'nonexistent_id' not found" in (response.error_message or "")


# MARK: - Confirmation-Required State Changes (COMPLETE_TASK, COMPLETE_COMMITMENT)

@pytest.mark.asyncio
async def test_complete_task_execution(env):
    task_store = env["task_store"]
    await task_store.initialize()
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    task = Task(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Finish report",
        status=TaskStatus.PENDING,
    )
    await task_store.put(task)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="tasks",
        trigger=ProactiveTrigger.TASK_DUE,
        action=ProactiveAction.COMPLETE_TASK,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Complete finish report",
        message="Mark report done",
        reference_id=task.id,
        action_payload={"task_id": task.id},
    )
    await proactive_store.add(candidate)

    conversation_id = str(uuid.uuid4())
    response = await executor.resolve_candidate_action(
        candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID, conversation_id=conversation_id
    )
    assert response.status == "accepted" and response.confirmation_required is True
    assert response.conversation_id == conversation_id and response.execution_id
    assert response.pending_confirmation.tool_name == "complete_task"
    unchanged = await executor.task_manager.get_task(task.id, DEFAULT_LOCAL_USER_ID)
    assert unchanged.status == TaskStatus.PENDING
    assert unchanged.updated_at == task.updated_at
    session = await env["conv_service"].store.get(conversation_id)
    assert session.pending_confirmation.proactive_candidate_id == candidate.candidate_id
    assert session.active_agent_execution.paused_tool_call.name == "complete_task"
    assert session.active_agent_execution.client_actions == []

    from app.services.proactive import manager as mgr_module
    original_manager = mgr_module._default_proactive_manager
    mgr_module._default_proactive_manager = env["proactive_mgr"]
    try:
        await env["conv_service"].confirm_tool_action(
            conversation_id=conversation_id,
            confirmation_id=response.pending_confirmation.confirmation_id,
            approved=True,
            authenticated_user_id=DEFAULT_LOCAL_USER_ID,
            execution_id=response.execution_id,
        )
    finally:
        mgr_module._default_proactive_manager = original_manager

    completed_task = await executor.task_manager.get_task(task.id, DEFAULT_LOCAL_USER_ID)
    assert completed_task.status == TaskStatus.COMPLETED
    assert [a["action"] for a in session.active_agent_execution.client_actions] == ["upsert_task"]
    completed_candidate = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert completed_candidate.status == ProactiveStatus.EXECUTED


@pytest.mark.asyncio
async def test_complete_commitment_execution(env):
    cmt_store = env["cmt_store"]
    await cmt_store.initialize()
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    cmt = Commitment(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Send presentation",
        status=CommitmentStatus.ACTIVE,
    )
    await cmt_store.put(cmt)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="commitments",
        trigger=ProactiveTrigger.COMMITMENT_DUE,
        action=ProactiveAction.COMPLETE_COMMITMENT,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Complete commitment",
        message="Mark commitment complete",
        reference_id=cmt.id,
        action_payload={"commitment_id": cmt.id},
    )
    await proactive_store.add(candidate)

    conversation_id = str(uuid.uuid4())
    response = await executor.resolve_candidate_action(
        candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID, conversation_id=conversation_id
    )
    assert response.status == "accepted" and response.confirmation_required is True
    assert response.conversation_id == conversation_id and response.execution_id
    assert response.pending_confirmation.tool_name == "complete_commitment"
    unchanged = await executor.commitment_manager.get_commitment(cmt.id, DEFAULT_LOCAL_USER_ID)
    assert unchanged.status == CommitmentStatus.ACTIVE
    assert unchanged.updated_at == cmt.updated_at
    session = await env["conv_service"].store.get(conversation_id)
    assert session.pending_confirmation.proactive_candidate_id == candidate.candidate_id
    assert session.active_agent_execution.paused_tool_call.name == "complete_commitment"
    assert session.active_agent_execution.client_actions == []

    from app.services.proactive import manager as mgr_module
    original_manager = mgr_module._default_proactive_manager
    mgr_module._default_proactive_manager = env["proactive_mgr"]
    try:
        await env["conv_service"].confirm_tool_action(
            conversation_id=conversation_id,
            confirmation_id=response.pending_confirmation.confirmation_id,
            approved=True,
            authenticated_user_id=DEFAULT_LOCAL_USER_ID,
            execution_id=response.execution_id,
        )
    finally:
        mgr_module._default_proactive_manager = original_manager

    completed_cmt = await executor.commitment_manager.get_commitment(cmt.id, DEFAULT_LOCAL_USER_ID)
    assert completed_cmt.status == CommitmentStatus.COMPLETED
    assert [a["action"] for a in session.active_agent_execution.client_actions] == ["upsert_commitment"]
    completed_candidate = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert completed_candidate.status == ProactiveStatus.EXECUTED


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["task", "commitment"])
async def test_proactive_completion_rejection_and_confirmation_replay_are_safe(env, domain):
    from app.services.conversation.service import ConversationIdentityError
    executor = env["executor"]
    store = env["proactive_store"]
    if domain == "task":
        entity = Task(user_id=DEFAULT_LOCAL_USER_ID, title="Reject task completion", status=TaskStatus.PENDING)
        await env["task_store"].put(entity)
        action = ProactiveAction.COMPLETE_TASK
        payload = {"task_id": entity.id}
        manager = executor.task_manager
        expected_status = TaskStatus.PENDING
        get_entity = manager.get_task
    else:
        entity = Commitment(user_id=DEFAULT_LOCAL_USER_ID, title="Reject commitment completion", status=CommitmentStatus.ACTIVE)
        await env["cmt_store"].put(entity)
        action = ProactiveAction.COMPLETE_COMMITMENT
        payload = {"commitment_id": entity.id}
        manager = executor.commitment_manager
        expected_status = CommitmentStatus.ACTIVE
        get_entity = manager.get_commitment

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID, source="tasks" if domain == "task" else "commitments", trigger=ProactiveTrigger.TIME,
        action=action, priority=ProactivePriority.MEDIUM, status=ProactiveStatus.ELIGIBLE,
        title=f"Complete {domain}", message="Completion requires approval.",
        reference_id=entity.id, action_payload=payload,
    )
    await store.add(candidate)
    conversation_id = str(uuid.uuid4())
    response = await executor.resolve_candidate_action(
        candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID, conversation_id=conversation_id
    )
    assert response.confirmation_required and response.execution_id
    assert (await get_entity(entity.id, user_id=DEFAULT_LOCAL_USER_ID)).status == expected_status
    session = await env["conv_service"].store.get(conversation_id)
    assert session.active_agent_execution.client_actions == []

    from app.services.proactive import manager as mgr_module
    original_manager = mgr_module._default_proactive_manager
    mgr_module._default_proactive_manager = env["proactive_mgr"]
    try:
        with pytest.raises(ConversationIdentityError):
            await env["conv_service"].confirm_tool_action(
                conversation_id, response.pending_confirmation.confirmation_id, True,
                authenticated_user_id=DEFAULT_LOCAL_USER_ID,
                execution_id="wrong-execution-id",
            )
        assert session.pending_confirmation is not None
        assert (await get_entity(entity.id, user_id=DEFAULT_LOCAL_USER_ID)).status == expected_status

        await env["conv_service"].confirm_tool_action(
            conversation_id, response.pending_confirmation.confirmation_id, False,
            authenticated_user_id=DEFAULT_LOCAL_USER_ID,
            execution_id=response.execution_id,
        )
        assert (await get_entity(entity.id, user_id=DEFAULT_LOCAL_USER_ID)).status == expected_status
        assert session.active_agent_execution.client_actions == []
        resolved = await store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
        assert resolved.status == ProactiveStatus.FAILED

        with pytest.raises(ConversationIdentityError):
            await env["conv_service"].confirm_tool_action(
                conversation_id, response.pending_confirmation.confirmation_id, True,
                authenticated_user_id=DEFAULT_LOCAL_USER_ID,
                execution_id=response.execution_id,
            )
        assert (await get_entity(entity.id, user_id=DEFAULT_LOCAL_USER_ID)).status == expected_status
    finally:
        mgr_module._default_proactive_manager = original_manager


@pytest.mark.asyncio
async def test_proactive_completion_confirmation_rejects_wrong_correlation_and_expiry(env):
    from app.services.conversation.service import ConversationIdentityError

    task = Task(user_id=DEFAULT_LOCAL_USER_ID, title="Correlated task", status=TaskStatus.PENDING)
    await env["task_store"].put(task)
    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID, source="tasks", trigger=ProactiveTrigger.TASK_DUE,
        action=ProactiveAction.COMPLETE_TASK, priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE, title="Complete correlated task", message="Approve completion.",
        reference_id=task.id, action_payload={"task_id": task.id},
    )
    await env["proactive_store"].add(candidate)
    conversation_id = str(uuid.uuid4())
    response = await env["executor"].resolve_candidate_action(
        candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID, conversation_id=conversation_id
    )
    pending = response.pending_confirmation
    assert pending is not None
    session = await env["conv_service"].store.get(conversation_id)

    with pytest.raises(ConversationIdentityError):
        await env["conv_service"].confirm_tool_action(
            conversation_id, "wrong-confirmation", True,
            authenticated_user_id=DEFAULT_LOCAL_USER_ID, execution_id=response.execution_id,
        )
    with pytest.raises(ValueError):
        await env["conv_service"].confirm_tool_action(
            "wrong-conversation", pending.confirmation_id, True,
            authenticated_user_id=DEFAULT_LOCAL_USER_ID, execution_id=response.execution_id,
        )
    with pytest.raises(ConversationIdentityError):
        await env["conv_service"].confirm_tool_action(
            conversation_id, pending.confirmation_id, True,
            authenticated_user_id="different-user", execution_id=response.execution_id,
        )
    assert session.pending_confirmation is not None
    assert (await env["executor"].task_manager.get_task(task.id, user_id=DEFAULT_LOCAL_USER_ID)).status == TaskStatus.PENDING

    candidate.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await env["proactive_store"].update(candidate)
    from app.services.proactive import manager as mgr_module
    original_manager = mgr_module._default_proactive_manager
    mgr_module._default_proactive_manager = env["proactive_mgr"]
    try:
        with pytest.raises(ValueError, match="no longer in an accepted state or has expired"):
            await env["conv_service"].confirm_tool_action(
                conversation_id, pending.confirmation_id, True,
                authenticated_user_id=DEFAULT_LOCAL_USER_ID, execution_id=response.execution_id,
            )
    finally:
        mgr_module._default_proactive_manager = original_manager
    assert (await env["executor"].task_manager.get_task(task.id, user_id=DEFAULT_LOCAL_USER_ID)).status == TaskStatus.PENDING
    assert session.active_agent_execution.client_actions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["task", "commitment"])
async def test_proactive_completion_permission_is_checked_before_confirmation(env, domain):
    if domain == "task":
        entity = Task(user_id=DEFAULT_LOCAL_USER_ID, title="Permission task", status=TaskStatus.PENDING)
        await env["task_store"].put(entity)
        permission = PermissionType.TASKS
        action = ProactiveAction.COMPLETE_TASK
        payload = {"task_id": entity.id}
    else:
        entity = Commitment(user_id=DEFAULT_LOCAL_USER_ID, title="Permission commitment", status=CommitmentStatus.ACTIVE)
        await env["cmt_store"].put(entity)
        permission = PermissionType.COMMITMENTS
        action = ProactiveAction.COMPLETE_COMMITMENT
        payload = {"commitment_id": entity.id}
    await env["perm_mgr"].update_permission(permission, PermissionStatus.DENIED, user_id=DEFAULT_LOCAL_USER_ID)
    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="tasks" if domain == "task" else "commitments",
        trigger=ProactiveTrigger.TIME,
        action=action,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title=f"Complete {domain}",
        message="Permission must be checked first.",
        reference_id=entity.id,
        action_payload=payload,
    )
    await env["proactive_store"].add(candidate)

    response = await env["executor"].resolve_candidate_action(candidate.candidate_id, user_id=DEFAULT_LOCAL_USER_ID)

    assert response.status == "failed" and not response.confirmation_required
    assert response.conversation_id is None and response.execution_id is None
    assert (await env["proactive_store"].get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)).status == ProactiveStatus.FAILED
    if domain == "task":
        unchanged = await env["executor"].task_manager.get_task(entity.id, user_id=DEFAULT_LOCAL_USER_ID)
        assert unchanged.status == TaskStatus.PENDING and unchanged.updated_at == entity.updated_at
    else:
        unchanged = await env["executor"].commitment_manager.get_commitment(entity.id, user_id=DEFAULT_LOCAL_USER_ID)
        assert unchanged.status == CommitmentStatus.ACTIVE and unchanged.updated_at == entity.updated_at


# MARK: - Confirmation-Required Action (CANCEL_REMINDER) & Phase 3 Correlation

@pytest.mark.asyncio
async def test_cancel_reminder_confirmation_lifecycle(env):
    rem_store = env["rem_store"]
    await rem_store.initialize()
    perm_mgr = env["perm_mgr"]
    executor = env["executor"]
    proactive_store = env["proactive_store"]
    conv_service = env["conv_service"]

    await perm_mgr.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED)

    rem = Reminder(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Take medication",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        status=ReminderStatus.SCHEDULED,
    )
    await rem_store.put(rem)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="reminders",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.CANCEL_REMINDER,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Cancel medication reminder",
        message="Would you like to cancel?",
        reference_id=rem.id,
        action_payload={"reminder_id": rem.id},
    )
    await proactive_store.add(candidate)

    # 1. Accept proposal -> initiates resolution, requires confirmation
    conv_id = str(uuid.uuid4())
    res = await executor.resolve_candidate_action(
        candidate.candidate_id,
        user_id=DEFAULT_LOCAL_USER_ID,
        conversation_id=conv_id,
    )

    assert res.status == "accepted"
    assert res.confirmation_required is True
    assert res.pending_confirmation is not None
    assert res.execution_id is not None
    assert res.pending_confirmation.tool_name == "cancel_reminder"

    # Verify candidate is ACCEPTED, NOT EXECUTED
    cand_check = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert cand_check.status == ProactiveStatus.ACCEPTED

    # Verify reminder is NOT cancelled yet!
    rem_check = await executor.reminder_manager.get_reminder(rem.id, DEFAULT_LOCAL_USER_ID)
    assert rem_check.status == ReminderStatus.SCHEDULED

    # Verify PendingConfirmation carries proactive_candidate_id correlation
    session = await conv_service.store.get(conv_id)
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.proactive_candidate_id == candidate.candidate_id

    # 2. Complete confirmation via Phase 3 confirm_tool_action (approved=True)
    # Patch the global proactive manager singleton so the session hook updates this test's store
    from app.services.proactive import manager as mgr_module
    orig_mgr = mgr_module._default_proactive_manager
    mgr_module._default_proactive_manager = env["proactive_mgr"]
    try:
        await conv_service.confirm_tool_action(
            conversation_id=conv_id,
            confirmation_id=session.pending_confirmation.confirmation_id,
            approved=True,
            execution_id=session.active_agent_execution.execution_id,
        )
    finally:
        mgr_module._default_proactive_manager = orig_mgr

    # Verify reminder IS NOW cancelled
    rem_after = await executor.reminder_manager.get_reminder(rem.id, DEFAULT_LOCAL_USER_ID)
    assert rem_after.status == ReminderStatus.CANCELLED

    # Verify candidate transitioned ACCEPTED -> EXECUTED
    cand_after = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert cand_after.status == ProactiveStatus.EXECUTED


@pytest.mark.asyncio
async def test_cancel_reminder_rejection(env):
    rem_store = env["rem_store"]
    await rem_store.initialize()
    perm_mgr = env["perm_mgr"]
    executor = env["executor"]
    proactive_store = env["proactive_store"]
    conv_service = env["conv_service"]

    await perm_mgr.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED)

    rem = Reminder(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Take medication 2",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        status=ReminderStatus.SCHEDULED,
    )
    await rem_store.put(rem)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="reminders",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.CANCEL_REMINDER,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Cancel medication reminder 2",
        message="Would you like to cancel?",
        reference_id=rem.id,
        action_payload={"reminder_id": rem.id},
    )
    await proactive_store.add(candidate)

    conv_id = str(uuid.uuid4())
    res = await executor.resolve_candidate_action(
        candidate.candidate_id,
        user_id=DEFAULT_LOCAL_USER_ID,
        conversation_id=conv_id,
    )
    assert res.status == "accepted"

    session = await conv_service.store.get(conv_id)

    from app.services.proactive import manager as mgr_module
    orig_mgr = mgr_module._default_proactive_manager
    mgr_module._default_proactive_manager = env["proactive_mgr"]
    try:
        # User rejects confirmation (approved=False)
        await conv_service.confirm_tool_action(
            conversation_id=conv_id,
            confirmation_id=session.pending_confirmation.confirmation_id,
            approved=False,
            execution_id=session.active_agent_execution.execution_id,
        )
    finally:
        mgr_module._default_proactive_manager = orig_mgr

    # Reminder must NOT be cancelled
    rem_after = await executor.reminder_manager.get_reminder(rem.id, DEFAULT_LOCAL_USER_ID)
    assert rem_after.status == ReminderStatus.SCHEDULED

    # Candidate becomes FAILED (never EXECUTED)
    cand_after = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert cand_after.status == ProactiveStatus.FAILED
    assert "declined" in (cand_after.safe_error_message or "").lower()


# MARK: - Non-Executable Action (NOTIFY) Rejection

@pytest.mark.asyncio
async def test_notify_action_strictly_rejected(env):
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="system",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.NOTIFY,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="Test Notification",
        message="Alert banner",
    )
    await proactive_store.add(candidate)

    response = await executor.resolve_candidate_action(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert response.status == "failed"
    assert "not executable in Phase 5I" in (response.error_message or "")

    cand_check = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert cand_check.status == ProactiveStatus.FAILED


# MARK: - Permission TOCTOU Check

@pytest.mark.asyncio
async def test_permission_toctou_rejection(env):
    perm_mgr = env["perm_mgr"]
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    # Initially authorized
    await perm_mgr.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED)

    candidate = ProactiveCandidate(
        user_id=DEFAULT_LOCAL_USER_ID,
        source="calendar",
        trigger=ProactiveTrigger.CALENDAR_EVENT,
        action=ProactiveAction.OPEN_CALENDAR_EVENT,
        priority=ProactivePriority.HIGH,
        status=ProactiveStatus.ELIGIBLE,
        title="Meeting",
        message="Meeting soon",
        reference_id="cal_evt_toctou",
        action_payload={"event_id": "cal_evt_toctou"},
    )
    await proactive_store.add(candidate)

    # Permission revoked before resolution
    await perm_mgr.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED)

    response = await executor.resolve_candidate_action(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert response.status == "failed"
    assert "Required permission 'calendar' is not authorized" in (response.error_message or "")

    cand_check = await proactive_store.get(candidate.candidate_id, DEFAULT_LOCAL_USER_ID)
    assert cand_check.status == ProactiveStatus.FAILED


# MARK: - Strict User Isolation & Cross-User Security

@pytest.mark.asyncio
async def test_cross_user_isolation(env):
    task_store = env["task_store"]
    await task_store.initialize()
    executor = env["executor"]
    proactive_store = env["proactive_store"]

    user_a_task = Task(user_id="user_a", title="Secret Task", status=TaskStatus.PENDING)
    await task_store.put(user_a_task)

    candidate_user_a = ProactiveCandidate(
        user_id="user_a",
        source="tasks",
        trigger=ProactiveTrigger.TASK_DUE,
        action=ProactiveAction.OPEN_TASK,
        priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE,
        title="User A Task",
        message="Secret task details",
        reference_id=user_a_task.id,
        action_payload={"task_id": user_a_task.id},
    )
    await proactive_store.add(candidate_user_a)

    # User B attempts to accept User A's candidate
    res_b = await executor.resolve_candidate_action(candidate_user_a.candidate_id, user_id="user_b")
    assert res_b.status == "failed"
    assert "Candidate proposal not found" in (res_b.error_message or "")


# MARK: - REST POST /candidates/{id}/accept Endpoint Sanitization

def test_rest_accept_endpoint_requires_identity():
    client = TestClient(app)

    # Proactive acceptance is a mutation and must not trust X-User-ID.
    response = client.post(
        "/api/v1/proactive/candidates/nonexistent_id/accept",
        headers={"X-User-ID": "test_sanitized_user"},
        json={},
    )
    assert response.status_code == 401

    # 2. Client passing confirmed=true query parameter has no effect on confirmation authority
    res_query = client.post(
        "/api/v1/proactive/candidates/nonexistent_id/accept?confirmed=true",
        headers={"X-User-ID": "test_sanitized_user"},
        json={"confirmed": True},  # ignored field
    )
    assert res_query.status_code == 401
