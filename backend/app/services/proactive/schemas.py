"""
Phase 5I — Schemas for Proactive Resolution, Navigation, and Public Responses.

Invariants:
- Strictly excludes reference_id, metadata, action_payload, raw tool output,
  internal execution results, internal exceptions, and stack traces.
- NavigationTarget contains strictly 'type' and 'id'.
- AcceptProactiveRequest contains strictly optional 'conversation_id' (NO confirmed boolean).
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class NavigationTargetType(str, Enum):
    """Supported entity types for client-side UI navigation."""
    TASK = "task"
    COMMITMENT = "commitment"
    CALENDAR_EVENT = "calendar_event"
    REMINDER = "reminder"


class NavigationTarget(BaseModel):
    """
    Safe public-facing typed navigation target.
    Contains strictly the minimum information required for client UI navigation.
    """
    type: NavigationTargetType
    id: str = Field(..., description="Minimum public-safe opaque identifier for the entity.")


class ProactivePendingConfirmationOut(BaseModel):
    """Safe public-facing pending confirmation description."""
    confirmation_id: str
    tool_name: str
    risk_level: str
    description: str


class AcceptProactiveRequest(BaseModel):
    """
    Request model to accept a proactive candidate proposal.
    Strictly NO 'confirmed' boolean or client-provided payload.
    """
    conversation_id: Optional[str] = Field(
        None,
        description="Optional conversation session ID to attach pending confirmation.",
    )


class ProactiveResolutionResponse(BaseModel):
    """
    Public resolution response.
    Exposes only safe, sanitized fields.
    """
    candidate_id: str
    status: str  # "accepted", "executed", "failed"
    confirmation_required: bool = False
    conversation_id: Optional[str] = None
    execution_id: Optional[str] = None
    pending_confirmation: Optional[ProactivePendingConfirmationOut] = None
    navigation_target: Optional[NavigationTarget] = None
    message: Optional[str] = None
    error_message: Optional[str] = None
