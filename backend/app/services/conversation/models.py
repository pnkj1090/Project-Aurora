import uuid
from enum import Enum
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, PrivateAttr

from app.services.tools.schemas import ToolCall
from app.services.web.citations.registry import CitationRegistry


class AgentExecutionStatus(str, Enum):
    """
    Lifecycle status for an agent execution loop.
    """
    RUNNING = "running"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ActiveExecutionConflictError(Exception):
    """Raised when an operation conflicts with an active running or confirmation-waiting agent execution."""
    pass


class StepExecutionTrace(BaseModel):
    """
    Structured operational telemetry for a single tool execution step.
    Strictly contains operational metadata: zero model chain-of-thought,
    private deliberation, or internal reasoning tokens are stored.
    """
    execution_id: str
    step_index: int
    tool_name: str
    tool_call_id: str
    status: str  # "started", "completed", "failed", "requires_confirmation", "cancelled"
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    risk_level: str
    confirmation_required: bool
    confirmation_status: Optional[str] = None  # "pending", "approved", "rejected"
    sanitized_result_summary: Optional[str] = None
    sanitized_error_summary: Optional[str] = None


class AgentExecutionState(BaseModel):
    """
    In-memory, ephemeral state of an active multi-step agent execution.
    Stored exclusively in ConversationSession in InMemoryConversationStore.
    """
    execution_id: str = Field(default_factory=lambda: f"exec-{uuid.uuid4().hex[:12]}")
    conversation_id: str
    user_id: str
    current_step: int = 0
    max_tool_iterations: int = 5
    execution_status: AgentExecutionStatus = AgentExecutionStatus.RUNNING
    traces: List[StepExecutionTrace] = Field(default_factory=list)
    paused_tool_call: Optional[ToolCall] = None
    pending_confirmation_id: Optional[str] = None
    step_activities: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None

    # Phase 5L.4: Ephemeral execution-scoped snapshots and exact-once guards.
    # Never persistent; not a secondary conversation history.
    turn_user_text: Optional[str] = None
    final_assistant_text: Optional[str] = None
    memory_extraction_started: bool = False
    memory_extraction_completed: bool = False

    # Phase 6B: Ephemeral client notification actions harvested from tool execution
    client_actions: List[Dict[str, Any]] = Field(default_factory=list)

    # Phase 5K.4: Execution-scoped citation registry.
    # PrivateAttr ensures this is excluded from model_dump(), .json(), and schema
    # generation — it is NEVER serialized into any API response or persisted.
    # model_copy() may preserve private attrs; the pause/resume guarantee comes
    # from holding the same AgentExecutionState object in memory throughout the
    # execution lifecycle (InMemoryConversationStore never serializes this object).
    _citation_registry: CitationRegistry = PrivateAttr(default_factory=CitationRegistry)

    @property
    def citation_registry(self) -> CitationRegistry:
        """Execution-scoped citation registry. Never serialized. Never persisted."""
        return self._citation_registry

    @property
    def is_active(self) -> bool:
        """Returns True if the execution is actively running or waiting for user confirmation."""
        return self.execution_status in (
            AgentExecutionStatus.RUNNING,
            AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        )

    def add_activity(self, activity: str) -> None:
        """Records a safe, user-facing high-level activity description."""
        clean = activity.strip()
        if clean and clean not in self.step_activities:
            self.step_activities.append(clean)
            self.updated_at = datetime.now(timezone.utc)
