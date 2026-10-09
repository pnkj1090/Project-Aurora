from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class ContactSnapshot(BaseModel):
    """
    Strongly typed, read-only contact representation.
    CRITICAL PRIVACY RULES:
    - Only minimal fields for explicit assistant lookup are preserved.
    - Postal addresses, birthdays, notes, social profiles, URLs, photos,
      and linked accounts are strictly excluded.
    - Phone numbers and email addresses are sensitive and must never be logged.
    """
    contact_id: str = Field(..., description="Unique contact identifier from source provider")
    display_name: str = Field(..., description="Full or primary display name")
    phone_numbers: List[str] = Field(default_factory=list, description="List of associated phone numbers")
    email_addresses: List[str] = Field(default_factory=list, description="List of associated email addresses")
    organization: Optional[str] = Field(default=None, description="Company or organization name")
    source: str = Field(default="ios_contacts", description="Authoritative origin of contact record")
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID, description="Target Aurora user identity")
    captured_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when the snapshot was captured (timezone-aware UTC)"
    )

    @field_validator("contact_id")
    @classmethod
    def validate_contact_id(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("contact_id cannot be empty or whitespace.")
        return trimmed

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("display_name cannot be empty or whitespace.")
        if len(trimmed) > 200:
            raise ValueError("display_name exceeds maximum length of 200 characters.")
        return trimmed

    @field_validator("organization")
    @classmethod
    def validate_organization(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        trimmed = v.strip()
        if not trimmed:
            return None
        if len(trimmed) > 200:
            raise ValueError("organization exceeds maximum length of 200 characters.")
        return trimmed

    @field_validator("phone_numbers", mode="before")
    @classmethod
    def validate_phone_numbers(cls, v: any) -> List[str]:
        if not isinstance(v, list):
            return []
        cleaned = []
        for item in v:
            if isinstance(item, str) and item.strip():
                cleaned.append(item.strip())
        return cleaned

    @field_validator("email_addresses", mode="before")
    @classmethod
    def validate_email_addresses(cls, v: any) -> List[str]:
        if not isinstance(v, list):
            return []
        cleaned = []
        for item in v:
            if isinstance(item, str) and item.strip():
                cleaned.append(item.strip().lower())
        return cleaned

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("captured_at must be a timezone-aware datetime.")
        return v
