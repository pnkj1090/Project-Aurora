from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

DEFAULT_LOCAL_USER_ID = "usr_local_default"


class UserIdentity(BaseModel):
    """
    Domain contract representing the owner/user of an Aurora installation.
    Vendor/provider-neutral and decoupled from authentication mechanisms.
    """
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID)
    display_name: Optional[str] = Field(default="Aurora User")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DeviceIdentity(BaseModel):
    """
    Domain contract representing a physical client installation (e.g., an iOS device)
    associated with an Aurora user.
    """
    device_id: str
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID)
    platform: str = Field(default="ios")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
