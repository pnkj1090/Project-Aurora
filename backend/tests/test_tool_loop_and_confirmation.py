import pytest
import pytest_asyncio
import hashlib
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.ai.base import BaseAIProvider, ChatMessage, AIResponse
from app.services.conversation.service import ConversationService
from app.services.conversation.store import InMemoryConversationStore
from app.services.conversation import get_conversation_service
from app.services.tools.schemas import ToolCall
from app.services.tools.registry import create_default_tool_registry
from app.services.tools.executor import ToolExecutor
from app.services.identity.auth import IdentityResolver, SQLiteCredentialStore, get_identity_resolver_dependency


class ToolCallingMockProvider(BaseAIProvider):
    """
    Predictable mock AI provider for testing tool calling loops and confirmations.
    """
    def __init__(self):
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "mock-tool-provider"

    async def generate_response(self, messages, tools=None, **kwargs) -> AIResponse:
        self.call_count += 1
        last_msg = messages[-1]

        # If last message was a tool result, synthesize final answer
        if last_msg.role.value == "tool":
            return AIResponse(
                content=f"Synthesized response using tool result: {last_msg.content}",
                provider=self.provider_name,
                model="test-model"
            )

        # Trigger low-risk tool call
        if "calculate" in last_msg.content:
            return AIResponse(
                content="",
                provider=self.provider_name,
                model="test-model",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(name="calculate", arguments={"expression": "50 * 2"})]
            )

        # Trigger confirmation-required tool call
        if "remind" in last_msg.content:
            return AIResponse(
                content="",
                provider=self.provider_name,
                model="test-model",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(name="create_test_reminder", arguments={"title": "Buy Milk", "datetime": "5pm"})]
            )

        # Normal text response
        return AIResponse(
            content=f"Normal answer to: {last_msg.content}",
            provider=self.provider_name,
            model="test-model"
        )


@pytest_asyncio.fixture
async def authenticated_headers(tmp_path):
    store = SQLiteCredentialStore(str(tmp_path / "confirm-api-auth.db"))
    secret = await store.issue_provisioning()
    credential = await store.redeem_provisioning(hashlib.sha256(secret.encode("ascii")).hexdigest())
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: IdentityResolver(store)
    yield {"Authorization": f"Bearer {credential.credential}"}
    app.dependency_overrides.pop(get_identity_resolver_dependency, None)


@pytest.fixture
def tool_service():
    registry = create_default_tool_registry()
    provider = ToolCallingMockProvider()
    store = InMemoryConversationStore()
    executor = ToolExecutor(registry=registry)
    return ConversationService(
        store=store,
        ai_provider=provider,
        tool_registry=registry,
        tool_executor=executor
    )


# MARK: - Tool Loop Service Tests

@pytest.mark.asyncio
async def test_automatic_tool_execution_loop(tool_service):
    # Ask for calculation -> should call tool automatically, get result 100, and return final synthesized response
    session, assistant_msg, ai_resp, pending, _ = await tool_service.process_chat(
        user_text="Please calculate this for me"
    )

    assert pending is None
    assert "Synthesized response using tool result" in assistant_msg.content
    assert "100" in assistant_msg.content

    # Check that session recorded user message, assistant tool call, tool result, and final assistant message
    messages = session.messages
    roles = [m.role.value for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert messages[2].tool_call_id is not None
    assert messages[2].name == "calculate"


@pytest.mark.asyncio
async def test_tool_confirmation_pause_and_approval(tool_service):
    # 1. Ask for reminder -> requires confirmation
    session, assistant_msg, ai_resp, pending, _ = await tool_service.process_chat(
        user_text="Please remind me"
    )

    assert pending is not None
    assert pending.tool_call.name == "create_test_reminder"
    assert session.pending_confirmation is not None
    conf_id = pending.confirmation_id

    # 2. Approve the action
    session2, final_msg, _, _ = await tool_service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=conf_id,
        approved=True,
        execution_id=session.active_agent_execution.execution_id,
    )

    assert session2.pending_confirmation is None
    assert "Synthesized response using tool result" in final_msg.content
    assert "scheduled" in final_msg.content


@pytest.mark.asyncio
async def test_tool_confirmation_rejection(tool_service):
    # 1. Trigger confirmation
    session, _, _, pending, _ = await tool_service.process_chat(user_text="Please remind me")
    assert pending is not None

    # 2. Reject the action
    session2, final_msg, _, _ = await tool_service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=pending.confirmation_id,
        approved=False,
        execution_id=session.active_agent_execution.execution_id,
    )

    assert session2.pending_confirmation is None
    # Verify tool result recorded the cancellation
    tool_msg = session2.messages[-2]
    assert tool_msg.role.value == "tool"
    assert "declined" in tool_msg.content


# MARK: - HTTP API Integration Tests

@pytest.mark.asyncio
async def test_api_chat_confirmation_and_confirm_endpoints(tool_service, authenticated_headers):
    app.dependency_overrides[get_conversation_service] = lambda: tool_service
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Send chat requesting reminder
        resp = await client.post("/api/v1/chat", json={"message": "Please remind me"}, headers=authenticated_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "confirmation_required"
        assert data["confirmation_id"] is not None
        assert data["tool"]["name"] == "create_test_reminder"
        assert data["tool"]["risk_level"] == "medium"

        conv_id = data["conversation_id"]
        conf_id = data["confirmation_id"]
        execution_id = data["execution_id"]

        # 2. Confirm the action via /api/v1/chat/confirm
        confirm_resp = await client.post("/api/v1/chat/confirm", json={
            "conversation_id": conv_id,
            "confirmation_id": conf_id,
            "execution_id": execution_id,
            "approved": True
        }, headers=authenticated_headers)
        assert confirm_resp.status_code == 200
        c_data = confirm_resp.json()
        assert c_data["type"] == "message"
        assert "Synthesized response" in c_data["message"]["content"]

        # 3. Verify /tools endpoint
        tools_resp = await client.get("/api/v1/tools")
        assert tools_resp.status_code == 200
        tools_data = tools_resp.json()
        tool_names = [t["name"] for t in tools_data]
        assert "calculate" in tool_names
        assert "create_test_reminder" in tool_names

    app.dependency_overrides.clear()
