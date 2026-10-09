from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class CalendarEvent(BaseModel):
    """
    Read-only calendar event representation for situational schedule context.
    Represents an ephemeral event snapshot from the device's calendar provider.
    Never persisted to SQLite, database, or disk.
    Strictly excludes attendee emails, organizer emails, URLs, tokens, attachments,
    and recurrence internals for data minimization.
    """
    event_id: str = Field(..., description="Unique event identifier from calendar provider")
    title: str = Field(..., description="Event title / summary")
    start_at: datetime = Field(..., description="Timezone-aware start datetime")
    end_at: datetime = Field(..., description="Timezone-aware end datetime")
    all_day: bool = Field(default=False, description="Whether this is an all-day event")
    calendar_name: Optional[str] = Field(default=None, description="Name of the calendar containing this event")
    location: Optional[str] = Field(default=None, description="Event location text (sensitive, never logged)")
    notes_available: bool = Field(default=False, description="Boolean flag indicating whether notes exist (content omitted)")
    source: str = Field(default="ios_eventkit", description="Source provider identifier")
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID, description="User identity scoping this event")

    @field_validator("event_id", mode="after")
    @classmethod
    def validate_event_id(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("event_id cannot be empty")
        return s

    @field_validator("title", mode="after")
    @classmethod
    def validate_title(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("title cannot be empty")
        return s

    @field_validator("start_at", "end_at", mode="after")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware (naive datetimes are not permitted)")
        return v

    @model_validator(mode="after")
    def validate_time_range(self) -> "CalendarEvent":
        if self.start_at and self.end_at:
            if self.end_at < self.start_at:
                raise ValueError("end_at cannot be earlier than start_at")
        return self
