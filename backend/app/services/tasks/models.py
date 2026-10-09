import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class TaskStatus(str, Enum):
    """
    Lifecycle states for an actionable task.
    """
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    """
    Priority levels for tasks and commitments.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Task(BaseModel):
    """
    Domain model representing an actionable unit of work.
    Distinct from conversational memories and intentions.
    """
    id: str = Field(default_factory=lambda: f"tsk_{uuid.uuid4().hex[:10]}")
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID)
    title: str = Field(min_length=1, max_length=500, description="Task title.")
    description: Optional[str] = Field(default="", max_length=5000, description="Task notes or description.")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    due_at: Optional[datetime] = Field(default=None, description="Optional UTC due datetime.")
    completed_at: Optional[datetime] = Field(default=None, description="Timestamp when task was marked completed.")

    tags: List[str] = Field(default_factory=list, description="Categorization tags.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Internal domain metadata.")
    is_active: bool = Field(default=True, description="Lifecycle active flag for safe soft deletion.")
    source_memory_id: Optional[str] = Field(default=None, description="Optional internal reference to originating memory.")
    source_commitment_id: Optional[str] = Field(default=None, description="Optional reference to originating commitment.")

    @field_validator("created_at", "updated_at", "due_at", "completed_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure all timestamps are timezone-aware in UTC."""
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @property
    def is_completed(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    @property
    def is_cancelled(self) -> bool:
        return self.status == TaskStatus.CANCELLED

    @property
    def task_id(self) -> str:
        return self.id

    @property
    def commitment_id(self) -> Optional[str]:
        return self.source_commitment_id
