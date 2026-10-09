import pytest
import httpx
from app.services.ai.base import (
    ChatMessage,
    MessageRole,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIUnavailableError,
)
from app.services.ai.openai_provider import OpenAIProvider


@pytest.mark.asyncio
async def test_openai_missing_api_key():
    provider = OpenAIProvider(api_key="")
    messages = [ChatMessage(role=MessageRole.USER, content="Hello")]

    with pytest.raises(AIAuthenticationError) as exc_info:
        await provider.generate_response(messages)

    assert "missing" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_openai_successful_response():
    def mock_handler(request: httpx.Request):
        assert request.headers["Authorization"] == "Bearer test-key-12345"
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Hello, I am Aurora!"
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {"total_tokens": 42}
            }
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAIProvider(api_key="test-key-12345", default_model="gpt-4o-mini", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        resp = await provider.generate_response(messages)
        assert resp.content == "Hello, I am Aurora!"
        assert resp.provider == "openai"
        assert resp.model == "gpt-4o-mini"
        assert resp.metadata["usage"]["total_tokens"] == 42


@pytest.mark.asyncio
async def test_openai_auth_failure_401():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "Invalid API key"}}))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAIProvider(api_key="invalid-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AIAuthenticationError):
            await provider.generate_response(messages)


@pytest.mark.asyncio
async def test_openai_rate_limit_429():
    transport = httpx.MockTransport(lambda req: httpx.Response(429, json={"error": {"message": "Rate limit reached"}}))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAIProvider(api_key="test-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AIRateLimitError):
            await provider.generate_response(messages)


@pytest.mark.asyncio
async def test_openai_timeout():
    def timeout_handler(req):
        raise httpx.TimeoutException("Connection timed out")

    transport = httpx.MockTransport(timeout_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAIProvider(api_key="test-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AITimeoutError):
            await provider.generate_response(messages)


@pytest.mark.asyncio
async def test_openai_server_error_503():
    transport = httpx.MockTransport(lambda req: httpx.Response(503, text="Service Unavailable"))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAIProvider(api_key="test-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AIUnavailableError):
            await provider.generate_response(messages)
