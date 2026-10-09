from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field, field_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class LocationSnapshot(BaseModel):
    """
    Ephemeral current-location snapshot value object.
    Represents an instantaneous observation, not location history or tracking.
    Never persisted to database or disk.
    """
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees (-90.0 to 90.0)")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees (-180.0 to 180.0)")
    accuracy_meters: float = Field(..., ge=0.0, description="Estimated horizontal accuracy in meters (>= 0.0)")
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of location observation")
    source: str = Field(default="ios_core_location", description="Origin provider of this snapshot")
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID, description="User identity scoping this snapshot")

    @field_validator("captured_at", mode="after")
    @classmethod
    def ensure_utc(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure observation timestamp is timezone-aware in UTC."""
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    def is_fresh(self, max_age_seconds: int = 900) -> bool:
        """
        Evaluates whether this snapshot falls within the freshness window (default: 15 minutes).
        Stale snapshots must not be presented as current location.
        """
        now = datetime.now(timezone.utc)
        age = (now - self.captured_at).total_seconds()
        return 0 <= age <= max_age_seconds
