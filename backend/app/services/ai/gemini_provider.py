import json
import logging
import uuid
import asyncio
from typing import List, Optional, Dict, Any, Tuple
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

logger = logging.getLogger("aurora.ai.gemini")


class GeminiProvider(BaseAIProvider):
    """
    Real AI Provider adapter for Google Gemini REST API (v1beta)
    using header-based authentication (x-goog-api-key) and native tool calling.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = "gemini-3.8-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
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
        return "gemini"

    def _format_messages_for_gemini(
        self, messages: List[ChatMessage]
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Translates Aurora ChatMessage list into Gemini REST format:
        - System messages are extracted into system_instruction
        - User and Assistant messages map to "user" and "model"
        - Tool results map into "user" functionResponse parts
        """
        system_parts = []
        contents: List[Dict[str, Any]] = []

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                system_parts.append({"text": msg.content})

            elif msg.role == MessageRole.USER:
                contents.append({
                    "role": "user",
                    "parts": [{"text": msg.content}]
                })

            elif msg.role == MessageRole.ASSISTANT:
                parts = []
                if msg.content:
                    parts.append({"text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        part_dict: Dict[str, Any] = {
                            "functionCall": {
                                "name": tc.name,
                                "args": tc.arguments if isinstance(tc.arguments, dict) else {}
                            }
                        }
                        if tc.id:
                            part_dict["functionCall"]["id"] = tc.id
                        if tc.metadata and "thoughtSignature" in tc.metadata:
                            part_dict["thoughtSignature"] = tc.metadata["thoughtSignature"]
                        parts.append(part_dict)
                if not parts:
                    parts.append({"text": ""})
                contents.append({
                    "role": "model",
                    "parts": parts
                })

            elif msg.role == MessageRole.TOOL:
                try:
                    parsed_response = json.loads(msg.content)
                except Exception:
                    parsed_response = {"output": msg.content}

                if not isinstance(parsed_response, dict):
                    parsed_response = {"result": parsed_response}

                contents.append({
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": msg.name or "tool",
                                "response": parsed_response
                            }
                        }
                    ]
                })

        system_instruction = {"parts": system_parts} if system_parts else None
        return system_instruction, contents

    def _format_tools_for_gemini(
        self, tools: List[ToolDefinition]
    ) -> List[Dict[str, Any]]:
        """
        Translates Aurora's generic ToolDefinitions into Gemini's function_declarations format.
        """
        function_declarations = []
        for tool_def in tools:
            function_declarations.append({
                "name": tool_def.name,
                "description": tool_def.description,
                "parameters": tool_def.parameters_schema
            })
        return [{"function_declarations": function_declarations}]

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
        Generate completion via Gemini v1beta generateContent REST endpoint.
        """
        if not self.api_key or not self.api_key.strip():
            raise AIAuthenticationError(
                "Gemini API key is missing. Please configure GEMINI_API_KEY or AI_API_KEY in the backend environment.",
                provider=self.provider_name
            )

        selected_model = model or self.default_model
        selected_temp = temperature if temperature is not None else self.default_temperature
        selected_max_tokens = max_tokens if max_tokens is not None else self.default_max_tokens
        request_timeout = timeout if timeout is not None else self.default_timeout

        system_instruction, contents = self._format_messages_for_gemini(messages)

        payload: Dict[str, Any] = {
            "contents": contents
        }

        if system_instruction:
            payload["system_instruction"] = system_instruction

        if tools:
            payload["tools"] = self._format_tools_for_gemini(tools)

        generation_config: Dict[str, Any] = {}
        if selected_temp is not None:
            generation_config["temperature"] = selected_temp
        if selected_max_tokens is not None:
            generation_config["maxOutputTokens"] = selected_max_tokens
        if response_format is not None or kwargs.get("response_format"):
            generation_config["responseMimeType"] = "application/json"
        if generation_config:
            payload["generationConfig"] = generation_config

        headers = {
            "x-goog-api-key": self.api_key.strip(),
            "Content-Type": "application/json",
            "User-Agent": "ProjectAurora/0.3.0",
        }

        endpoint = f"{self.base_url}/models/{selected_model}:generateContent"

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                if self._http_client is not None:
                    response = await self._http_client.post(endpoint, headers=headers, json=payload)
                else:
                    async with httpx.AsyncClient(timeout=request_timeout) as client:
                        response = await client.post(endpoint, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                logger.warning("Gemini request timed out after %.1fs", request_timeout)
                raise AITimeoutError(f"Request timed out after {request_timeout} seconds.", provider=self.provider_name) from exc
            except httpx.RequestError as exc:
                logger.error("Network error communicating with Gemini: %s", type(exc).__name__)
                raise AIUnavailableError("Network error while communicating with AI provider.", provider=self.provider_name) from exc

            if response.status_code == 503 and attempt < max_retries and self._http_client is None:
                logger.warning("Gemini 503 high demand spike. Retrying in %ds...", attempt + 1)
                await asyncio.sleep(attempt + 1)
                continue
            break

        # Handle HTTP error status codes
        if response.status_code in (401, 403):
            logger.error("Gemini authentication failed (HTTP %d)", response.status_code)
            raise AIAuthenticationError("Invalid or unauthorized AI provider API key.", provider=self.provider_name)

        if response.status_code == 429:
            logger.warning("Gemini rate limit / quota exceeded (HTTP 429)")
            raise AIRateLimitError("AI provider rate limit or usage quota exceeded.", provider=self.provider_name)

        if response.status_code >= 500:
            logger.error("Gemini server error (HTTP %d)", response.status_code)
            raise AIUnavailableError("AI provider service is temporarily unavailable.", provider=self.provider_name)

        if response.status_code != 200:
            logger.error("Gemini unexpected response status HTTP %d: %s", response.status_code, response.text[:200])
            raise AIProviderError(f"Unexpected response from AI provider (HTTP {response.status_code}).", provider=self.provider_name)

        try:
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                # Check for prompt blocking or empty candidates
                prompt_feedback = data.get("promptFeedback", {})
                block_reason = prompt_feedback.get("blockReason")
                if block_reason:
                    raise AIProviderError(f"Prompt was blocked by safety filters: {block_reason}", provider=self.provider_name)
                raise AIProviderError("No response candidates returned by AI provider.", provider=self.provider_name)

            first_candidate = candidates[0]
            content_obj = first_candidate.get("content", {})
            parts = content_obj.get("parts", [])
            finish_reason = first_candidate.get("finishReason", "STOP").lower()
            usage = data.get("usageMetadata", {})

            text_segments = []
            parsed_tool_calls: Optional[List[ToolCall]] = None

            for part in parts:
                if "text" in part:
                    text_segments.append(part["text"])
                if "functionCall" in part:
                    if parsed_tool_calls is None:
                        parsed_tool_calls = []
                    fn = part["functionCall"]
                    fn_name = fn.get("name", "")
                    fn_args = fn.get("args", {})
                    fn_id = fn.get("id") or f"call_{fn_name}_{uuid.uuid4().hex[:6]}"
                    thought_sig = part.get("thoughtSignature")
                    meta = {"thoughtSignature": thought_sig} if thought_sig else {}
                    parsed_tool_calls.append(ToolCall(
                        id=fn_id,
                        name=fn_name,
                        arguments=fn_args if isinstance(fn_args, dict) else {},
                        metadata=meta
                    ))

            final_content = "".join(text_segments)
            final_finish_reason = "tool_calls" if parsed_tool_calls else finish_reason

            return AIResponse(
                content=final_content,
                provider=self.provider_name,
                model=selected_model,
                finish_reason=final_finish_reason,
                tool_calls=parsed_tool_calls,
                metadata={
                    "usage": usage,
                    "index": first_candidate.get("index", 0),
                }
            )
        except Exception as exc:
            if isinstance(exc, AIProviderError):
                raise
            logger.error("Failed to parse Gemini response: %s", str(exc))
            raise AIProviderError("Failed to parse response from AI provider.", provider=self.provider_name) from exc
