from datetime import datetime, timezone
from typing import Optional, Dict
from app.services.identity.models import UserIdentity, DeviceIdentity, DEFAULT_LOCAL_USER_ID


class IdentityManager:
    """
    Manages local and runtime identity contracts.
    Provider-neutral and designed to allow real authentication providers
    to be added in the future without changing domain services.
    """

    def __init__(self, default_user: Optional[UserIdentity] = None):
        self._current_user = default_user or UserIdentity()
        self._devices: Dict[str, DeviceIdentity] = {}

    def get_current_user(self) -> UserIdentity:
        """Returns the current resolved user identity."""
        return self._current_user

    def set_current_user(self, user: UserIdentity) -> None:
        """Sets the active user identity (e.g. for multi-user or test sessions)."""
        self._current_user = user

    def get_or_create_device(
        self,
        device_id: str,
        platform: str = "ios",
        user_id: Optional[str] = None
    ) -> DeviceIdentity:
        """
        Retrieves or registers an installation device for the specified user.
        """
        if not device_id or not device_id.strip():
            raise ValueError("device_id cannot be empty")

        clean_id = device_id.strip()
        uid = user_id or self._current_user.user_id

        if clean_id in self._devices:
            dev = self._devices[clean_id]
            dev.last_active_at = datetime.now(timezone.utc)
            return dev

        new_device = DeviceIdentity(
            device_id=clean_id,
            user_id=uid,
            platform=platform,
            last_active_at=datetime.now(timezone.utc)
        )
        self._devices[clean_id] = new_device
        return new_device


default_identity_manager = IdentityManager()
