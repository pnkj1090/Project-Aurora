from enum import Enum
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Union
from pydantic import BaseModel, Field

from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class PermissionType(str, Enum):
    """
    Domain capabilities/resources that require platform or user authorization.
    """
    LOCATION = "location"
    CALENDAR = "calendar"
    NOTIFICATIONS = "notifications"
    CONTACTS = "contacts"
    REMINDERS = "reminders"
    PHOTOS_FILES = "photos_files"
    TASKS = "tasks"
    COMMITMENTS = "commitments"


class PermissionStatus(str, Enum):
    """
    Authorization states matching platform permission lifecycles.
    """
    NOT_DETERMINED = "not_determined"
    DENIED = "denied"
    AUTHORIZED = "authorized"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"


class PermissionRecord(BaseModel):
    """
    Domain model representing the abstract authorization state of a permission.
    In Phase 5A, this represents runtime domain state. The authoritative source
    of real device permissions is the operating system via platform providers.
    """
    permission_type: Union[PermissionType, str]
    status: PermissionStatus = PermissionStatus.NOT_DETERMINED
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID)
    source: str = Field(default="system", description="Origin of permission report (e.g. system, default, test)")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_authorized(self) -> bool:
        """
        Only explicitly AUTHORIZED satisfies a required permission.
        States 'not_determined', 'denied', 'restricted', and 'unavailable' return False.
        """
        return self.status == PermissionStatus.AUTHORIZED
