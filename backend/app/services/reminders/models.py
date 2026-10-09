import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class ReminderStatus(str, Enum):
    """
    Lifecycle states for an explicit user reminder.
    """
    SCHEDULED = "scheduled"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Reminder(BaseModel):
    """
    Domain model representing an explicitly scheduled reminder.
    Platform-neutral: actual delivery authority is iOS UNUserNotificationCenter.
    """
    id: str = Field(default_factory=lambda: f"rem_{uuid.uuid4().hex[:10]}")
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID)
    title: str = Field(min_length=1, max_length=500, description="Reminder title.")
    body: Optional[str] = Field(default="", max_length=5000, description="Optional reminder message or body.")
    scheduled_at: datetime = Field(description="Target UTC datetime when reminder should trigger.")
    status: ReminderStatus = Field(default=ReminderStatus.SCHEDULED)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    task_id: Optional[str] = Field(default=None, description="Optional user-scoped task ID.")
    commitment_id: Optional[str] = Field(default=None, description="Optional user-scoped commitment ID.")
    notification_id: Optional[str] = Field(default=None, description="Identifier for client-side local notification.")
    is_active: bool = Field(default=True, description="Safe soft-deletion flag.")

    @field_validator("created_at", "updated_at", "scheduled_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure all timestamps are timezone-aware in UTC."""
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @property
    def is_scheduled(self) -> bool:
        return self.status == ReminderStatus.SCHEDULED

    @property
    def is_delivered(self) -> bool:
        return self.status == ReminderStatus.DELIVERED

    @property
    def is_cancelled(self) -> bool:
        return self.status == ReminderStatus.CANCELLED

    @property
    def is_failed(self) -> bool:
        return self.status == ReminderStatus.FAILED

    @property
    def reminder_id(self) -> str:
        return self.id
