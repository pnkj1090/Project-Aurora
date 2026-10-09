import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class CommitmentStatus(str, Enum):
    """
    Lifecycle states for tracked user commitments and intentions.
    """
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CommitmentPriority(str, Enum):
    """
    Priority levels for commitments.
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Commitment(BaseModel):
    """
    Domain model representing an intentional obligation, agreement, or promise
    to track over time toward completion.
    Distinct from concrete tasks and conversational memories.
    """
    id: str = Field(default_factory=lambda: f"cmt_{uuid.uuid4().hex[:10]}")
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID)
    title: str = Field(min_length=1, max_length=500, description="Commitment title or agreement.")
    description: Optional[str] = Field(default="", max_length=5000, description="Details or context of commitment.")
    status: CommitmentStatus = Field(default=CommitmentStatus.ACTIVE)
    priority: CommitmentPriority = Field(default=CommitmentPriority.MEDIUM)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    target_date: Optional[datetime] = Field(default=None, description="Optional target completion date in UTC.")
    completed_at: Optional[datetime] = Field(default=None, description="Timestamp when commitment was marked completed.")

    tags: List[str] = Field(default_factory=list, description="Categorization tags.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Internal domain metadata.")
    is_active: bool = Field(default=True, description="Lifecycle active flag for safe soft deletion.")
    source_memory_id: Optional[str] = Field(default=None, description="Optional internal reference to originating memory.")

    @field_validator("created_at", "updated_at", "target_date", "completed_at", mode="after")
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
        return self.status == CommitmentStatus.COMPLETED

    @property
    def is_cancelled(self) -> bool:
        return self.status == CommitmentStatus.CANCELLED

    @property
    def commitment_id(self) -> str:
        return self.id
