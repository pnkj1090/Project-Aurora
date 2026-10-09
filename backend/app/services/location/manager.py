import logging
from typing import Optional, Dict, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.location.models import LocationSnapshot

logger = logging.getLogger("aurora.services.location")


class LocationManager:
    """
    Orchestration layer governing ephemeral location snapshots, permission gating,
    freshness validation, and user identity scoping.
    Strictly in-memory: does not persist historical locations or coordinates to disk.
    """

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self._permission_manager = permission_manager or default_permission_manager
        # Ephemeral in-memory store: user_id -> LocationSnapshot
        self._snapshots: Dict[str, LocationSnapshot] = {}

    @property
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager

    async def is_location_authorized(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> bool:
        """Checks if PermissionType.LOCATION is currently AUTHORIZED for the user."""
        return await self._permission_manager.is_authorized(
            PermissionType.LOCATION,
            user_id=user_id
        )

    def set_current_location(
        self,
        snapshot: LocationSnapshot,
        user_id: Optional[str] = None
    ) -> LocationSnapshot:
        """
        Stores an ephemeral current location snapshot for the user identity.
        Replaces any prior snapshot. Does not retain history.
        Never logs raw coordinates.
        """
        uid = user_id or snapshot.user_id
        stored = snapshot.model_copy(deep=True)
        stored.user_id = uid
        self._snapshots[uid] = stored
        logger.info("Updated ephemeral location snapshot for user '%s' (source: %s)", uid, stored.source)
        return stored

    async def get_current_location(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        max_age_seconds: int = 900
    ) -> Tuple[Optional[LocationSnapshot], str]:
        """
        Retrieves the current location snapshot for the active user if authorized and fresh.
        Returns: (snapshot: Optional[LocationSnapshot], status_reason: str)
        status_reason values: "available", "unauthorized", "unavailable", "expired"
        """
        # 1. Authorization check
        is_auth = await self.is_location_authorized(user_id=user_id)
        if not is_auth:
            logger.info("Location request blocked: user '%s' not authorized", user_id)
            return None, "unauthorized"

        # 2. Availability check
        snapshot = self._snapshots.get(user_id)
        if snapshot is None:
            logger.info("Location request: no snapshot available for user '%s'", user_id)
            return None, "unavailable"

        # 3. Freshness check
        if not snapshot.is_fresh(max_age_seconds=max_age_seconds):
            logger.info("Location request: snapshot expired for user '%s'", user_id)
            return None, "expired"

        return snapshot.model_copy(deep=True), "available"

    def clear_location(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> None:
        """Clears the ephemeral snapshot for the user."""
        self._snapshots.pop(user_id, None)
        logger.info("Cleared ephemeral location snapshot for user '%s'", user_id)


# Global singleton instance
_default_location_manager: Optional[LocationManager] = None


def get_location_manager(
    permission_manager: Optional[PermissionManager] = None
) -> LocationManager:
    """Retrieve or construct the LocationManager instance."""
    global _default_location_manager
    if permission_manager is not None:
        return LocationManager(permission_manager=permission_manager)
    if _default_location_manager is None:
        _default_location_manager = LocationManager()
    return _default_location_manager


def get_location_manager_dependency() -> LocationManager:
    """FastAPI dependency provider."""
    return get_location_manager()
