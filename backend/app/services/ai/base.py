from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from app.services.tools.schemas import ToolCall
from app.services.tools.base import ToolDefinition


# MARK: - AI Provider Exceptions

class AIProviderError(Exception):
    """Base exception for all AI provider failures."""
    def __init__(self, message: str, provider: str = "unknown"):
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class AIAuthenticationError(AIProviderError):
    """Raised when provider credentials (API key) are missing or invalid."""
    pass


class AIRateLimitError(AIProviderError):
    """Raised when provider rate limits or quotas are exceeded."""
    pass


class AITimeoutError(AIProviderError):
    """Raised when an AI request times out."""
    pass


class AIUnavailableError(AIProviderError):
    """Raised when the AI provider service is down or experiencing 5xx errors."""
    pass


# MARK: - Models

class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(BaseModel):
    role: MessageRole
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None


class AIResponse(BaseModel):
    content: str
    provider: str
    model: str
    finish_reason: str = "stop"
    tool_calls: Optional[List[ToolCall]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseAIProvider(ABC):
    """
    Abstract Base Class for all AI model providers (OpenAI, Gemini, Mock, etc.).
    Keeps the core application decoupled from any single vendor.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider, e.g. 'mock', 'openai', 'gemini'."""
        pass

    @abstractmethod
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
        Generate an AI completion given a conversation history and optional available tools.
        """
        pass
