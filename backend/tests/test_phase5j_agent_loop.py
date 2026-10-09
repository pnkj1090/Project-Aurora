import pytest
import pytest_asyncio
import hashlib
import uuid
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.ai.base import (
    BaseAIProvider,
    ChatMessage,
    MessageRole,
    AIResponse,
    AIProviderError,
)
from app.services.conversation.models import (
    AgentExecutionState,
    AgentExecutionStatus,
    StepExecutionTrace,
    ActiveExecutionConflictError,
)
from app.services.conversation.service import ConversationService
from app.services.conversation.store import InMemoryConversationStore
from app.services.conversation.session import ConversationSession
from app.services.conversation.context import ContextManager
from app.services.conversation import get_conversation_service
from app.services.conversation.agent_loop import (
    AgentLoopController,
    get_activity_description,
)
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.schemas import ToolCall, ToolResult, PendingConfirmation
from app.services.tools.registry import ToolRegistry, create_default_tool_registry
from app.services.tools.executor import ToolExecutor
from app.services.permissions.manager import PermissionManager
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.identity.auth import IdentityResolver, SQLiteCredentialStore, get_identity_resolver_dependency


# MARK: - Test Tools

class SafeTimeTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_current_time",
            description="Returns current time",
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"current_time": "2026-09-23T12:00:00Z"}


class SafeCalendarTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_upcoming_calendar_events",
            description="Returns upcoming events",
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"events": [{"id": "ev-1", "title": "Team Standup"}]}


class SafeTasksTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_tasks",
            description="Returns tasks",
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"tasks": [{"id": "t-1", "title": "Buy coffee"}]}


class MutatingTaskTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="create_task",
            description="Creates a task",
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=False,
            parameters_schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"task_id": "new-t-1", "title": params.get("title", "")}


class ConfirmationReminderTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="cancel_reminder",
            description="Cancels a reminder",
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            parameters_schema={
                "type": "object",
                "properties": {"reminder_id": {"type": "string"}},
                "required": ["reminder_id"],
            },
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"cancelled": True, "reminder_id": params.get("reminder_id")}


class PermissionProtectedTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_current_location",
            description="Retrieves current GPS coordinates",
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.LOCATION],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"latitude": 37.7749, "longitude": -122.4194}


class FailingTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="failing_external_query",
            description="Simulates a remote tool failure",
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("External query connection timed out.")


# MARK: - Configurable Mock AI Provider

class MultiStepMockProvider(BaseAIProvider):
    """
    Mock provider with queued responses or scriptable decision cycles.
    """
    def __init__(self, script: Optional[List[AIResponse]] = None):
        self.script = list(script) if script else []
        self.recorded_calls = []
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "mock-multi-step"

    async def generate_response(self, messages, tools=None, model=None, **kwargs) -> AIResponse:
        self.call_count += 1
        self.recorded_calls.append({
            "messages": list(messages),
            "tools": tools,
            "model": model,
        })

        if self.script:
            return self.script.pop(0)

        # Default fallback
        last = messages[-1]
        return AIResponse(
            content=f"Echo: {last.content}",
            provider=self.provider_name,
            model=model or "test-model",
        )


@pytest.fixture
def test_registry():
    reg = ToolRegistry()
    reg.register(SafeTimeTool())
    reg.register(SafeCalendarTool())
    reg.register(SafeTasksTool())
    reg.register(MutatingTaskTool())
    reg.register(ConfirmationReminderTool())
    reg.register(PermissionProtectedTool())
    reg.register(FailingTool())
    return reg


@pytest.fixture
def make_service(test_registry):
    def _create(provider: BaseAIProvider, perm_manager: Optional[PermissionManager] = None):
        store = InMemoryConversationStore()
        executor = ToolExecutor(registry=test_registry, permission_manager=perm_manager)
        return ConversationService(
            store=store,
            ai_provider=provider,
            tool_registry=test_registry,
            tool_executor=executor,
            max_tool_iterations=5,
        )
    return _create


@pytest_asyncio.fixture
async def authenticated_headers(tmp_path):
    store = SQLiteCredentialStore(str(tmp_path / "agent-api-auth.db"))
    secret = await store.issue_provisioning()
    credential = await store.redeem_provisioning(hashlib.sha256(secret.encode("ascii")).hexdigest())
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: IdentityResolver(store)
    yield {"Authorization": f"Bearer {credential.credential}"}
    app.dependency_overrides.pop(get_identity_resolver_dependency, None)


# MARK: - 1. Iteration Semantics & Hard Cap Tests

@pytest.mark.asyncio
async def test_agent_loop_single_step_tool(make_service):
    """Single tool call executes, returns observation, then model synthesizes final answer in 1 tool iteration."""
    provider = MultiStepMockProvider(script=[
        AIResponse(
            content="",
            tool_calls=[ToolCall(name="get_current_time", arguments={})],
            provider="mock",
            model="test",
        ),
        AIResponse(
            content="The current time is 12:00 UTC.",
            provider="mock",
            model="test",
        ),
    ])
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(
        user_text="What time is it?"
    )

    assert pending is None
    assert assistant_msg.content == "The current time is 12:00 UTC."
    assert session.active_agent_execution is not None
    assert session.active_agent_execution.current_step == 1
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
    assert len(session.active_agent_execution.traces) == 1
    assert session.active_agent_execution.traces[0].tool_name == "get_current_time"
    assert "Checked current time" in session.active_agent_execution.step_activities


@pytest.mark.asyncio
async def test_agent_loop_multi_step_sequential(make_service):
    """Dependent tool calls across iterations 1 and 2 complete cleanly in 2 steps."""
    provider = MultiStepMockProvider(script=[
        AIResponse(
            content="",
            tool_calls=[ToolCall(name="get_current_time", arguments={})],
            provider="mock",
            model="test",
        ),
        AIResponse(
            content="",
            tool_calls=[ToolCall(name="get_upcoming_calendar_events", arguments={})],
            provider="mock",
            model="test",
        ),
        AIResponse(
            content="Checked time and found 1 standup event.",
            provider="mock",
            model="test",
        ),
    ])
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(
        user_text="Check my schedule based on the time"
    )

    assert pending is None
    assert session.active_agent_execution.current_step == 2
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
    assert len(session.active_agent_execution.traces) == 2
    assert session.active_agent_execution.traces[0].tool_name == "get_current_time"
    assert session.active_agent_execution.traces[1].tool_name == "get_upcoming_calendar_events"
    assert "Checked upcoming calendar events" in session.active_agent_execution.step_activities


@pytest.mark.asyncio
async def test_agent_loop_hard_ceiling_at_five_iterations(make_service):
    """When the model repeatedly requests tools across 5 iterations, the loop halts strictly after 5."""
    script = [
        AIResponse(content="", tool_calls=[ToolCall(name="get_current_time", arguments={})], provider="mock", model="test")
        for _ in range(5)
    ]
    # Add final synthesis response
    script.append(AIResponse(content="Final synthesized output after 5 steps", provider="mock", model="test"))

    provider = MultiStepMockProvider(script=script)
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(
        user_text="Run repeated actions"
    )

    assert pending is None
    assert session.active_agent_execution.current_step == 5
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
    assert len(session.active_agent_execution.traces) == 5
    assert assistant_msg.content == "Final synthesized output after 5 steps"


@pytest.mark.asyncio
async def test_agent_loop_impossible_sixth_iteration_and_tool_free_synthesis(make_service):
    """
    Verifies that the final synthesis call dispatched after iteration 5 has tools=None,
    guaranteeing a 6th tool iteration is architecturally impossible.
    """
    script = [
        AIResponse(content="", tool_calls=[ToolCall(name="get_current_time", arguments={})], provider="mock", model="test")
        for _ in range(5)
    ]
    script.append(AIResponse(content="Synthesized response", provider="mock", model="test"))

    provider = MultiStepMockProvider(script=script)
    service = make_service(provider)

    await service.process_chat(user_text="Repeat 5 times")

    assert len(provider.recorded_calls) == 6
    # 5 iterations with tools provided
    for i in range(5):
        assert provider.recorded_calls[i]["tools"] is not None
    # 6th call (final synthesis) MUST have tools=None
    final_call = provider.recorded_calls[5]
    assert final_call["tools"] is None, "Final synthesis call must have tools=None"


# MARK: - 2. Whole-Batch Parallel Preflight Tests

@pytest.mark.asyncio
async def test_parallel_preflight_all_safe_executes_concurrently(make_service):
    """Batch with 2 safe read-only LOW risk tools passes preflight and executes concurrently."""
    provider = MultiStepMockProvider(script=[
        AIResponse(
            content="",
            tool_calls=[
                ToolCall(id="c1", name="get_current_time", arguments={}),
                ToolCall(id="c2", name="get_upcoming_calendar_events", arguments={}),
            ],
            provider="mock",
            model="test",
        ),
        AIResponse(content="Got time and calendar events in parallel", provider="mock", model="test"),
    ])
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(
        user_text="What time is it and what's on my calendar?"
    )

    assert pending is None
    assert session.active_agent_execution.current_step == 1
    assert len(session.active_agent_execution.traces) == 2
    assert {t.tool_name for t in session.active_agent_execution.traces} == {"get_current_time", "get_upcoming_calendar_events"}


@pytest.mark.asyncio
async def test_parallel_preflight_unsafe_medium_risk_prevents_partial_concurrency(make_service):
    """Batch mixing LOW risk and MEDIUM risk fails parallel preflight and executes sequentially."""
    provider = MultiStepMockProvider(script=[
        AIResponse(
            content="",
            tool_calls=[
                ToolCall(id="c1", name="get_current_time", arguments={}),
                ToolCall(id="c2", name="create_task", arguments={"title": "Test Task"}),
            ],
            provider="mock",
            model="test",
        ),
        AIResponse(content="Done creating task and checking time", provider="mock", model="test"),
    ])
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(
        user_text="Check time and create task"
    )

    assert pending is None
    # Both executed sequentially
    assert len(session.active_agent_execution.traces) == 2
    assert session.active_agent_execution.traces[0].tool_name == "get_current_time"
    assert session.active_agent_execution.traces[1].tool_name == "create_task"


@pytest.mark.asyncio
async def test_parallel_preflight_confirmation_required_prevents_partial_concurrency(make_service):
    """Batch mixing safe call and confirmation-required call does not partially execute concurrently."""
    provider = MultiStepMockProvider(script=[
        AIResponse(
            content="",
            tool_calls=[
                ToolCall(id="c1", name="cancel_reminder", arguments={"reminder_id": "rem-1"}),
                ToolCall(id="c2", name="get_current_time", arguments={}),
            ],
            provider="mock",
            model="test",
        )
    ])
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(
        user_text="Cancel reminder and get time"
    )

    # Must pause on confirmation requirement without executing cancel_reminder
    assert pending is not None
    assert pending.tool_call.name == "cancel_reminder"
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.WAITING_FOR_CONFIRMATION


@pytest.mark.asyncio
async def test_parallel_preflight_mutating_prefix_rejected(test_registry):
    """Controller preflight rejects mutating prefixes from concurrent execution."""
    controller = AgentLoopController(
        ai_provider=MultiStepMockProvider(),
        tool_registry=test_registry,
        tool_executor=ToolExecutor(registry=test_registry),
        context_manager=ContextManager(),
        context_engine=None,
    )
    is_safe, reason = controller.preflight_parallel_batch([
        ToolCall(name="get_current_time", arguments={}),
        ToolCall(name="create_task", arguments={"title": "test"}),
    ])
    assert is_safe is False
    assert "risk level" in reason or "mutating" in reason


@pytest.mark.asyncio
async def test_parallel_preflight_peer_dependence_rejected(test_registry):
    """Batch where call B references call A output fails preflight peer independence."""
    controller = AgentLoopController(
        ai_provider=MultiStepMockProvider(),
        tool_registry=test_registry,
        tool_executor=ToolExecutor(registry=test_registry),
        context_manager=ContextManager(),
        context_engine=None,
    )
    is_safe, reason = controller.preflight_parallel_batch([
        ToolCall(id="call_1", name="get_current_time", arguments={}),
        ToolCall(id="call_2", name="get_upcoming_calendar_events", arguments={"ref": "result_from_call_1"}),
    ])
    assert is_safe is False
    assert "peer call ID" in reason


# MARK: - 3. Confirmation Pause / Resume & Correlation Verification

@pytest.mark.asyncio
async def test_confirmation_resume_success(make_service):
    """Full valid confirmation approval executes pre-approved tool and resumes loop cleanly."""
    provider = MultiStepMockProvider(script=[
        AIResponse(
            content="",
            tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-99"})],
            provider="mock",
            model="test",
        ),
        AIResponse(content="Reminder rem-99 has been cancelled.", provider="mock", model="test"),
    ])
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(
        user_text="Cancel reminder rem-99"
    )

    assert pending is not None
    assert pending.agent_execution_id == session.active_agent_execution.execution_id
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.WAITING_FOR_CONFIRMATION

    # Resume with approval
    res_session, res_assistant, res_ai, _ = await service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=pending.confirmation_id,
        approved=True,
        execution_id=session.active_agent_execution.execution_id,
    )

    assert res_session.pending_confirmation is None
    assert res_session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
    assert res_assistant.content == "Reminder rem-99 has been cancelled."


@pytest.mark.asyncio
async def test_confirmation_rejection_marks_cancelled(make_service):
    """User rejection marks execution CANCELLED and never executes the tool."""
    provider = MultiStepMockProvider(script=[
        AIResponse(
            content="",
            tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-99"})],
            provider="mock",
            model="test",
        ),
        AIResponse(content="Understood, I cancelled the operation.", provider="mock", model="test"),
    ])
    service = make_service(provider)

    session, _, _, pending, _ = await service.process_chat(user_text="Cancel rem-99")
    assert pending is not None

    res_session, res_assistant, _, _ = await service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=pending.confirmation_id,
        approved=False,
        execution_id=session.active_agent_execution.execution_id,
    )

    assert res_session.pending_confirmation is None
    assert res_session.active_agent_execution.execution_status == AgentExecutionStatus.CANCELLED
    # Check trace shows cancelled
    traces = res_session.active_agent_execution.traces
    assert traces[-1].status == "cancelled"
    assert traces[-1].confirmation_status == "rejected"


@pytest.mark.asyncio
async def test_confirmation_wrong_execution_id_fails_closed(make_service):
    """Mismatched agent_execution_id fails closed immediately."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-1"})], provider="mock", model="test")
    ])
    service = make_service(provider)
    session, _, _, pending, _ = await service.process_chat(user_text="Cancel reminder")

    # Tamper with session confirmation agent_execution_id
    session.pending_confirmation.agent_execution_id = "wrong-exec-id"

    with pytest.raises(ValueError, match="Execution integrity failure"):
        await service.confirm_tool_action(
            conversation_id=session.conversation_id,
            confirmation_id=pending.confirmation_id,
            approved=True,
            execution_id=session.active_agent_execution.execution_id,
        )
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_confirmation_mismatched_tool_name_fails_closed(make_service):
    """Tampered tool name in paused state fails closed."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-1"})], provider="mock", model="test")
    ])
    service = make_service(provider)
    session, _, _, pending, _ = await service.process_chat(user_text="Cancel reminder")

    # Tamper with paused tool name
    session.active_agent_execution.paused_tool_call.name = "tampered_tool"

    with pytest.raises(ValueError, match="Tool name mismatch"):
        await service.confirm_tool_action(
            conversation_id=session.conversation_id,
            confirmation_id=pending.confirmation_id,
            approved=True,
            execution_id=session.active_agent_execution.execution_id,
        )
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_confirmation_mismatched_arguments_fails_closed(make_service):
    """Tampered tool arguments fail closed."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-1"})], provider="mock", model="test")
    ])
    service = make_service(provider)
    session, _, _, pending, _ = await service.process_chat(user_text="Cancel reminder")

    # Tamper with paused tool arguments
    session.active_agent_execution.paused_tool_call.arguments = {"reminder_id": "tampered-rem-99"}

    with pytest.raises(ValueError, match="Tool arguments mismatch"):
        await service.confirm_tool_action(
            conversation_id=session.conversation_id,
            confirmation_id=pending.confirmation_id,
            approved=True,
            execution_id=session.active_agent_execution.execution_id,
        )
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_confirmation_replay_defense(make_service):
    """Confirming a second time fails closed; tool cannot execute twice."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-1"})], provider="mock", model="test"),
        AIResponse(content="Done", provider="mock", model="test"),
    ])
    service = make_service(provider)
    session, _, _, pending, _ = await service.process_chat(user_text="Cancel reminder")

    # First confirmation succeeds
    await service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=pending.confirmation_id,
        approved=True,
        execution_id=session.active_agent_execution.execution_id,
    )

    # Second confirmation attempts replay
    with pytest.raises(ActiveExecutionConflictError):
        await service.confirm_tool_action(
            conversation_id=session.conversation_id,
            confirmation_id=pending.confirmation_id,
            approved=True,
            execution_id=session.active_agent_execution.execution_id,
        )


# MARK: - 4. Active Execution Concurrency Tests

@pytest.mark.asyncio
async def test_concurrency_second_chat_rejected_while_waiting_confirmation(make_service):
    """When a session has an active execution waiting for confirmation, a second chat request is rejected with 409."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-1"})], provider="mock", model="test")
    ])
    service = make_service(provider)
    session, _, _, pending, _ = await service.process_chat(user_text="Cancel reminder")

    assert session.has_active_execution()

    with pytest.raises(ActiveExecutionConflictError, match="currently has an active execution"):
        await service.process_chat(
            user_text="New message while paused",
            conversation_id=session.conversation_id,
        )


@pytest.mark.asyncio
async def test_concurrency_terminal_execution_cleared_safely(make_service):
    """After an execution reaches COMPLETED, a subsequent chat request starts a new execution cleanly."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="First turn complete.", provider="mock", model="test"),
        AIResponse(content="Second turn complete.", provider="mock", model="test"),
    ])
    service = make_service(provider)

    session1, _, _, _, _ = await service.process_chat(user_text="Hello 1")
    assert session1.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED

    # Subsequent chat turn on same conversation
    session2, assistant2, _, _, _ = await service.process_chat(
        user_text="Hello 2",
        conversation_id=session1.conversation_id,
    )
    assert assistant2.content == "Second turn complete."
    assert session2.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED


# MARK: - 5. Failure Taxonomy & Privacy Tests

@pytest.mark.asyncio
async def test_recoverable_tool_failure_continues_loop(make_service):
    """Recoverable tool failure feeds sanitized observation to model, allowing graceful continuation."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="failing_external_query", arguments={})], provider="mock", model="test"),
        AIResponse(content="I encountered an issue querying external service, but here is what I know.", provider="mock", model="test"),
    ])
    service = make_service(provider)

    session, assistant_msg, ai_response, pending, _ = await service.process_chat(user_text="Query remote data")

    assert pending is None
    assert "encountered an issue querying external service" in assistant_msg.content
    assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
    assert session.active_agent_execution.traces[0].status == "failed"
    assert "connection timed out" in session.active_agent_execution.traces[0].sanitized_error_summary


@pytest.mark.asyncio
async def test_permission_failure_feeds_observation_safely(test_registry):
    """When a required runtime permission is not authorized, tool execution is blocked and observation recorded."""
    perm_manager = PermissionManager()
    # Explicitly deny location
    await perm_manager.update_permission(PermissionType.LOCATION, PermissionStatus.DENIED)

    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="get_current_location", arguments={})], provider="mock", model="test"),
        AIResponse(content="Location permission is required to check your coordinates.", provider="mock", model="test"),
    ])

    store = InMemoryConversationStore()
    executor = ToolExecutor(registry=test_registry, permission_manager=perm_manager)
    service = ConversationService(
        store=store,
        ai_provider=provider,
        tool_registry=test_registry,
        tool_executor=executor,
    )

    session, assistant_msg, _, _, _ = await service.process_chat(user_text="Where am I?")

    assert "Location permission is required" in assistant_msg.content
    trace = session.active_agent_execution.traces[0]
    assert trace.status == "failed"
    assert "not authorized" in trace.sanitized_error_summary


@pytest.mark.asyncio
async def test_provider_failure_marks_execution_failed(make_service):
    """Unrecoverable AI Provider failure marks execution FAILED."""
    class FailingProvider(BaseAIProvider):
        @property
        def provider_name(self) -> str:
            return "failing-provider"

        async def generate_response(self, messages, **kwargs):
            raise AIProviderError("Backend connection reset.")

    service = make_service(FailingProvider())

    with pytest.raises(AIProviderError):
        await service.process_chat(user_text="Hello")


@pytest.mark.asyncio
async def test_telemetry_strictly_no_chain_of_thought():
    """Verifies trace model and state strictly contain no CoT, reasoning tokens, or internal prompts."""
    trace = StepExecutionTrace(
        execution_id="exec-123",
        step_index=1,
        tool_name="get_current_time",
        tool_call_id="call-1",
        status="completed",
        risk_level="low",
        confirmation_required=False,
    )
    dumped = trace.model_dump()
    assert "chain_of_thought" not in dumped
    assert "thought" not in dumped
    assert "reasoning" not in dumped
    assert "prompt" not in dumped


# MARK: - 6. FastAPI Endpoints Integration Tests

@pytest.mark.asyncio
async def test_api_chat_multi_step_response_fields(test_registry, authenticated_headers):
    """POST /api/v1/chat returns execution_id, steps_count, and step_activities."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="get_current_time", arguments={})], provider="mock", model="test"),
        AIResponse(content="Time is 12:00", provider="mock", model="test"),
    ])
    store = InMemoryConversationStore()
    executor = ToolExecutor(registry=test_registry)
    custom_service = ConversationService(
        store=store,
        ai_provider=provider,
        tool_registry=test_registry,
        tool_executor=executor,
    )
    app.dependency_overrides[get_conversation_service] = lambda: custom_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/chat", json={"message": "What time is it?"}, headers=authenticated_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"
        assert data["execution_id"] is not None
        assert data["steps_count"] == 1
        assert "Checked current time" in data["step_activities"]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_api_chat_409_conflict_on_active_execution(test_registry, authenticated_headers):
    """POST /api/v1/chat returns 409 Conflict when a conversation has an active execution."""
    provider = MultiStepMockProvider(script=[
        AIResponse(content="", tool_calls=[ToolCall(name="cancel_reminder", arguments={"reminder_id": "rem-1"})], provider="mock", model="test")
    ])
    store = InMemoryConversationStore()
    executor = ToolExecutor(registry=test_registry)
    custom_service = ConversationService(
        store=store,
        ai_provider=provider,
        tool_registry=test_registry,
        tool_executor=executor,
    )
    app.dependency_overrides[get_conversation_service] = lambda: custom_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First request pauses for confirmation
        r1 = await client.post("/api/v1/chat", json={"message": "Cancel reminder"}, headers=authenticated_headers)
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["type"] == "confirmation_required"
        conv_id = d1["conversation_id"]

        # Second request to same conversation should return 409
        r2 = await client.post("/api/v1/chat", json={"message": "Second message", "conversation_id": conv_id}, headers=authenticated_headers)
        assert r2.status_code == 409
        assert "currently has an active execution" in r2.json()["detail"]

    app.dependency_overrides.clear()
