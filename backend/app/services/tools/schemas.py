import uuid
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.services.tools.base import ToolRiskLevel


class ToolCall(BaseModel):
    """
    Provider-agnostic representation of a tool call requested by an AI model.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """
    Result of executing a tool, returned back to the AI Gateway.
    """
    tool_call_id: str
    tool_name: str
    output: Any
    success: bool = True
    error: Optional[str] = None


class PendingConfirmation(BaseModel):
    """
    Represents an action paused awaiting explicit user authorization.
    """
    confirmation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_call: ToolCall
    risk_level: ToolRiskLevel
    description: str
    proactive_candidate_id: Optional[str] = None
    agent_execution_id: Optional[str] = None
