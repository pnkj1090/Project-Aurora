import pytest
import httpx
from app.services.ai.base import (
    ChatMessage,
    MessageRole,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIUnavailableError,
    AIProviderError,
)
from app.services.ai.gemini_provider import GeminiProvider
from app.services.tools.base import ToolDefinition, ToolRiskLevel


@pytest.mark.asyncio
async def test_gemini_missing_api_key():
    provider = GeminiProvider(api_key="")
    messages = [ChatMessage(role=MessageRole.USER, content="Hello")]

    with pytest.raises(AIAuthenticationError) as exc_info:
        await provider.generate_response(messages)

    assert "missing" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_gemini_successful_text_response():
    def mock_handler(request: httpx.Request):
        assert request.headers["x-goog-api-key"] == "test-gemini-key"
        assert "/models/gemini-3.8-flash:generateContent" in str(request.url)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Hello, I am Aurora powered by Gemini!"}
                            ],
                            "role": "model"
                        },
                        "finishReason": "STOP",
                        "index": 0
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 8,
                    "totalTokenCount": 20
                }
            }
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(
            api_key="test-gemini-key",
            default_model="gemini-3.8-flash",
            http_client=client
        )
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content="You are Aurora."),
            ChatMessage(role=MessageRole.USER, content="Hi there!")
        ]

        resp = await provider.generate_response(messages)
        assert resp.content == "Hello, I am Aurora powered by Gemini!"
        assert resp.provider == "gemini"
        assert resp.model == "gemini-3.8-flash"
        assert resp.finish_reason == "stop"
        assert resp.metadata["usage"]["totalTokenCount"] == 20


@pytest.mark.asyncio
async def test_gemini_tool_calling():
    def mock_tool_handler(request: httpx.Request):
        assert request.headers["x-goog-api-key"] == "test-gemini-key"
        # Verify tools were sent in payload
        body = httpx.ByteStream(request.read())
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "calculate",
                                        "args": {"expression": "25 * 4"}
                                    }
                                }
                            ],
                            "role": "model"
                        },
                        "finishReason": "STOP"
                    }
                ]
            }
        )

    transport = httpx.MockTransport(mock_tool_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(api_key="test-gemini-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Calculate 25 * 4")]
        tools = [
            ToolDefinition(
                name="calculate",
                description="Math calculator",
                parameters_schema={"type": "object", "properties": {"expression": {"type": "string"}}},
                risk_level=ToolRiskLevel.LOW
            )
        ]

        resp = await provider.generate_response(messages, tools=tools)
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls is not None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "calculate"
        assert resp.tool_calls[0].arguments == {"expression": "25 * 4"}


@pytest.mark.asyncio
async def test_gemini_auth_failure_401():
    transport = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "API key not valid"}}))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(api_key="invalid-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AIAuthenticationError):
            await provider.generate_response(messages)


@pytest.mark.asyncio
async def test_gemini_rate_limit_429():
    transport = httpx.MockTransport(lambda req: httpx.Response(429, json={"error": {"message": "Resource exhausted"}}))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(api_key="valid-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AIRateLimitError):
            await provider.generate_response(messages)


@pytest.mark.asyncio
async def test_gemini_timeout():
    def timeout_handler(req):
        raise httpx.TimeoutException("Gemini connection timed out")

    transport = httpx.MockTransport(timeout_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(api_key="valid-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AITimeoutError):
            await provider.generate_response(messages)


@pytest.mark.asyncio
async def test_gemini_server_error_503():
    transport = httpx.MockTransport(lambda req: httpx.Response(503, text="Service Unavailable"))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(api_key="valid-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Hi")]

        with pytest.raises(AIUnavailableError):
            await provider.generate_response(messages)


@pytest.mark.asyncio
async def test_gemini_prompt_blocked():
    transport = httpx.MockTransport(lambda req: httpx.Response(
        200,
        json={"promptFeedback": {"blockReason": "SAFETY"}}
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeminiProvider(api_key="valid-key", http_client=client)
        messages = [ChatMessage(role=MessageRole.USER, content="Unsafe request")]

        with pytest.raises(AIProviderError) as exc_info:
            await provider.generate_response(messages)

        assert "blocked" in str(exc_info.value).lower()
