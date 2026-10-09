import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from app.services.ai.base import ChatMessage, MessageRole
from app.services.tools.schemas import ToolCall, PendingConfirmation
from app.services.conversation.models import AgentExecutionState, AgentExecutionStatus
from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class ConversationSession(BaseModel):
    """
    Representation of a conversation session with history, timestamps,
    and any pending tool confirmation.
    """
    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    messages: List[ChatMessage] = Field(default_factory=list)
    pending_confirmation: Optional[PendingConfirmation] = None
    active_agent_execution: Optional[AgentExecutionState] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def user_id(self) -> str:
        return self.metadata.get("user_id", DEFAULT_LOCAL_USER_ID)

    def has_active_execution(self) -> bool:
        """Returns True if an agent execution is actively running or awaiting confirmation."""
        return self.active_agent_execution is not None and self.active_agent_execution.is_active


    def add_message(
        self,
        role: MessageRole,
        content: str,
        tool_call_id: Optional[str] = None,
        tool_calls: Optional[List[ToolCall]] = None,
        name: Optional[str] = None
    ) -> ChatMessage:
        msg = ChatMessage(
            role=role,
            content=content,
            name=name,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls
        )
        self.messages.append(msg)
        self.updated_at = datetime.now(timezone.utc)
        return msg
