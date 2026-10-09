import pytest
import pytest_asyncio
import hashlib
from app.services.identity.auth import IdentityResolver, SQLiteCredentialStore, get_identity_resolver_dependency
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.ai.base import (
    BaseAIProvider,
    ChatMessage,
    AIResponse,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
)
from app.services.conversation import get_conversation_service
from app.services.conversation.service import ConversationService
from app.services.conversation.store import InMemoryConversationStore
from app.services.conversation.context import ContextManager


class StubProvider(BaseAIProvider):
    @property
    def provider_name(self) -> str:
        return "stub"

    async def generate_response(self, messages, **kwargs) -> AIResponse:
        return AIResponse(
            content=f"Echo: {messages[-1].content}",
            provider="stub",
            model="stub-model",
            finish_reason="stop"
        )


class FailingProvider(BaseAIProvider):
    def __init__(self, failure_type: str):
        self.failure_type = failure_type

    @property
    def provider_name(self) -> str:
        return "failing"

    async def generate_response(self, messages, **kwargs) -> AIResponse:
        if self.failure_type == "auth":
            raise AIAuthenticationError("Bad Key", provider="failing")
        elif self.failure_type == "rate":
            raise AIRateLimitError("Too Many Requests", provider="failing")
        elif self.failure_type == "timeout":
            raise AITimeoutError("Timed Out", provider="failing")
        raise RuntimeError("Unknown failure")


@pytest.fixture
def mock_service():
    store = InMemoryConversationStore()
    provider = StubProvider()
    return ConversationService(store=store, ai_provider=provider, context_manager=ContextManager())


@pytest_asyncio.fixture
async def auth_headers(tmp_path):
    store = SQLiteCredentialStore(str(tmp_path / "chat-auth.db"))
    secret = await store.issue_provisioning()
    result = await store.redeem_provisioning(hashlib.sha256(secret.encode("ascii")).hexdigest())
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: IdentityResolver(store)
    yield {"Authorization": f"Bearer {result.credential}"}
    app.dependency_overrides.pop(get_identity_resolver_dependency, None)


@pytest.mark.asyncio
async def test_health_check_regression():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "active_provider" in data
        assert "active_model" in data


@pytest.mark.asyncio
async def test_chat_new_and_continued_conversation(mock_service, auth_headers):
    app.dependency_overrides[get_conversation_service] = lambda: mock_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Turn 1
        resp1 = await client.post("/api/v1/chat", json={"message": "First message"}, headers=auth_headers)
        assert resp1.status_code == 200
        data1 = resp1.json()
        conv_id = data1["conversation_id"]
        assert conv_id is not None
        assert data1["message"]["role"] == "assistant"
        assert "First message" in data1["message"]["content"]

        # Turn 2 with existing conv_id
        resp2 = await client.post("/api/v1/chat", json={
            "conversation_id": conv_id,
            "message": "Second message"
        }, headers=auth_headers)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["conversation_id"] == conv_id

        # Verify conversation history endpoint
        hist_resp = await client.get(f"/api/v1/conversations/{conv_id}", headers=auth_headers)
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()
        assert len(hist_data["messages"]) == 4

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_invalid_empty_message(mock_service, auth_headers):
    app.dependency_overrides[get_conversation_service] = lambda: mock_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/chat", json={"message": ""}, headers=auth_headers)
        # Pydantic min_length=1 triggers 422
        assert resp.status_code in (400, 422)
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_chat_provider_error_handling(auth_headers):
    for f_type, expected_status in [
        ("auth", 401),
        ("rate", 429),
        ("timeout", 504),
    ]:
        store = InMemoryConversationStore()
        provider = FailingProvider(f_type)
        failing_svc = ConversationService(store=store, ai_provider=provider)
        app.dependency_overrides[get_conversation_service] = lambda: failing_svc

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/chat", json={"message": "Hello"}, headers=auth_headers)
            assert resp.status_code == expected_status, f"Expected {expected_status} for {f_type}, got {resp.status_code}"

    app.dependency_overrides.clear()
