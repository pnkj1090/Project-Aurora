import json
import logging
from typing import List, Optional, Dict, Any
import httpx

from app.services.ai.base import (
    BaseAIProvider,
    ChatMessage,
    MessageRole,
    AIResponse,
    AIProviderError,
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIUnavailableError,
)
from app.services.tools.base import ToolDefinition
from app.services.tools.schemas import ToolCall

logger = logging.getLogger("aurora.ai.openai")


class OpenAIProvider(BaseAIProvider):
    """
    Real AI Provider adapter for OpenAI chat completions with native tool calling support.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        default_timeout: float = 30.0,
        default_max_tokens: Optional[int] = 1024,
        default_temperature: float = 0.7,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = base_url.rstrip("/")
        self.default_timeout = default_timeout
        self.default_max_tokens = default_max_tokens
        self.default_temperature = default_temperature
        self._http_client = http_client

    @property
    def provider_name(self) -> str:
        return "openai"

    def _format_messages_for_openai(self, messages: List[ChatMessage]) -> List[Dict[str, Any]]:
        """Converts internal ChatMessage models to OpenAI chat completions format."""
        formatted = []
        for msg in messages:
            if msg.role == MessageRole.TOOL:
                formatted.append({
                    "role": "tool",
                    "content": msg.content,
                    "tool_call_id": msg.tool_call_id or ""
                })
            elif msg.role == MessageRole.ASSISTANT and msg.tool_calls:
                assistant_dict: Dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else str(tc.arguments)
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                }
                formatted.append(assistant_dict)
            else:
                formatted.append({
                    "role": msg.role.value,
                    "content": msg.content
                })
        return formatted

    async def generate_response(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[ToolDefinition]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AIResponse:
        """
        Generate completion via OpenAI REST API with function/tool definitions.
        """
        if not self.api_key or not self.api_key.strip():
            raise AIAuthenticationError(
                "OpenAI API key is missing. Please configure AI_API_KEY or OPENAI_API_KEY in the backend environment.",
                provider=self.provider_name
            )

        selected_model = model or self.default_model
        selected_temp = temperature if temperature is not None else self.default_temperature
        selected_max_tokens = max_tokens if max_tokens is not None else self.default_max_tokens
        request_timeout = timeout if timeout is not None else self.default_timeout

        formatted_messages = self._format_messages_for_openai(messages)

        payload: Dict[str, Any] = {
            "model": selected_model,
            "messages": formatted_messages,
            "temperature": selected_temp,
        }
        if selected_max_tokens:
            payload["max_tokens"] = selected_max_tokens

        # Format tool definitions for OpenAI if supplied
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_def.name,
                        "description": tool_def.description,
                        "parameters": tool_def.parameters_schema
                    }
                }
                for tool_def in tools
            ]
        if response_format is not None:
            payload["response_format"] = response_format
        elif "response_format" in kwargs and kwargs["response_format"] is not None:
            payload["response_format"] = kwargs["response_format"]

        headers = {
            "Authorization": f"Bearer {self.api_key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "ProjectAurora/0.3.0",
        }

        endpoint = f"{self.base_url}/chat/completions"

        try:
            if self._http_client is not None:
                response = await self._http_client.post(endpoint, headers=headers, json=payload)
            else:
                async with httpx.AsyncClient(timeout=request_timeout) as client:
                    response = await client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            logger.warning("OpenAI request timed out after %.1fs", request_timeout)
            raise AITimeoutError(f"Request timed out after {request_timeout} seconds.", provider=self.provider_name) from exc
        except httpx.RequestError as exc:
            logger.error("Network error communicating with OpenAI: %s", type(exc).__name__)
            raise AIUnavailableError("Network error while communicating with AI provider.", provider=self.provider_name) from exc

        # Handle HTTP status codes
        if response.status_code in (401, 403):
            logger.error("OpenAI authentication failed (HTTP %d)", response.status_code)
            raise AIAuthenticationError("Invalid or unauthorized AI provider API key.", provider=self.provider_name)

        if response.status_code == 429:
            logger.warning("OpenAI rate limit / quota exceeded (HTTP 429)")
            raise AIRateLimitError("AI provider rate limit or usage quota exceeded.", provider=self.provider_name)

        if response.status_code >= 500:
            logger.error("OpenAI server error (HTTP %d)", response.status_code)
            raise AIUnavailableError("AI provider service is temporarily unavailable.", provider=self.provider_name)

        if response.status_code != 200:
            logger.error("OpenAI unexpected response status HTTP %d: %s", response.status_code, response.text[:200])
            raise AIProviderError(f"Unexpected response from AI provider (HTTP {response.status_code}).", provider=self.provider_name)

        try:
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise AIProviderError("No response choices returned by AI provider.", provider=self.provider_name)

            first_choice = choices[0]
            message_obj = first_choice.get("message", {})
            content = message_obj.get("content") or ""
            finish_reason = first_choice.get("finish_reason", "stop")
            usage = data.get("usage", {})

            # Extract any tool calls from choice
            parsed_tool_calls: Optional[List[ToolCall]] = None
            raw_tool_calls = message_obj.get("tool_calls")
            if raw_tool_calls:
                parsed_tool_calls = []
                for tc in raw_tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    raw_args = fn.get("arguments", "{}")
                    try:
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        parsed_args = {}
                    parsed_tool_calls.append(ToolCall(
                        id=tc.get("id") or str(fn_name),
                        name=fn_name,
                        arguments=parsed_args
                    ))

            return AIResponse(
                content=content,
                provider=self.provider_name,
                model=data.get("model", selected_model),
                finish_reason=finish_reason or "stop",
                tool_calls=parsed_tool_calls,
                metadata={
                    "usage": usage,
                    "id": data.get("id"),
                }
            )
        except Exception as exc:
            if isinstance(exc, AIProviderError):
                raise
            logger.error("Failed to parse OpenAI response: %s", str(exc))
            raise AIProviderError("Failed to parse response from AI provider.", provider=self.provider_name) from exc
