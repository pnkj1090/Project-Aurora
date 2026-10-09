from typing import List, Optional, Dict, Any
from app.services.ai.base import BaseAIProvider, ChatMessage, AIResponse
from app.services.tools.base import ToolDefinition
from app.services.tools.schemas import ToolCall


class MockProvider(BaseAIProvider):
    """
    Offline mock provider for testing, foundation validation, and local development
    without external API credentials.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

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
        selected_model = model or "mock-v1"

        if response_format is not None or kwargs.get("response_format"):
            return AIResponse(
                content='{"proposals": []}',
                provider=self.provider_name,
                model=selected_model,
                finish_reason="stop",
                metadata={"structured": True, "message_count": len(messages)},
            )

        last_message = messages[-1].content if messages else "No message provided."

        # Support simulated tool call triggers for testing if tools are provided
        simulated_tool_calls: Optional[List[ToolCall]] = None
        
        # If the last message is a tool result, produce a final response referencing it
        if messages and messages[-1].role.value == "tool":
            return AIResponse(
                content=f"[Aurora Core - Mock Provider]: I received the tool result: {messages[-1].content}. Everything looks great!",
                provider=self.provider_name,
                model=selected_model,
                finish_reason="stop",
                metadata={"message_count": len(messages)}
            )

        # Trigger tool calls based on keywords if tools are available
        if tools:
            tool_names = [t.name for t in tools]
            lower_last = last_message.lower()

            if "time" in lower_last and "get_current_time" in tool_names:
                simulated_tool_calls = [
                    ToolCall(name="get_current_time", arguments={"timezone": "UTC"})
                ]
            elif "calculate" in lower_last and "calculate" in tool_names:
                simulated_tool_calls = [
                    ToolCall(name="calculate", arguments={"expression": "10 * 5 + 2"})
                ]
            elif "remind" in lower_last and "create_test_reminder" in tool_names:
                simulated_tool_calls = [
                    ToolCall(name="create_test_reminder", arguments={"title": "Test Reminder", "datetime": "tomorrow 9am"})
                ]

        if simulated_tool_calls:
            return AIResponse(
                content="",
                provider=self.provider_name,
                model=selected_model,
                finish_reason="tool_calls",
                tool_calls=simulated_tool_calls,
                metadata={"tool_call_count": len(simulated_tool_calls)}
            )

        response_text = (
            f"[Aurora Core - Mock Provider]: Received your message: '{last_message}'. "
            f"The backend AI gateway abstraction is functioning properly."
        )

        return AIResponse(
            content=response_text,
            provider=self.provider_name,
            model=selected_model,
            finish_reason="stop",
            metadata={
                "message_count": len(messages),
                "mock_status": "operational",
                "temperature": temperature or 0.7,
                "max_tokens": max_tokens
            }
        )
