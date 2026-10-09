import logging
from typing import Dict, Any, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.location import LocationManager, get_location_manager

logger = logging.getLogger("aurora.tools.location")


class GetCurrentLocationTool(BaseTool):
    """
    Tool allowing Aurora to inspect the user's current device location snapshot.
    Risk level: LOW.
    Confirmation: False (read-only query).
    Permission: Requires PermissionType.LOCATION to be AUTHORIZED.
    """

    def __init__(self, location_manager: Optional[LocationManager] = None):
        self._manager = location_manager

    @property
    def manager(self) -> LocationManager:
        return self._manager or get_location_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_current_location",
            description=(
                "Returns the user's current device location snapshot (latitude, longitude, accuracy). "
                "Requires explicit location authorization. Does not track history."
            ),
            parameters_schema={
                "type": "object",
                "properties": {},
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.LOCATION]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID

        snapshot, status_reason = await self.manager.get_current_location(user_id=user_id)

        if snapshot is None:
            if status_reason == "unauthorized":
                return {
                    "status": "error",
                    "error": "Location access is not authorized. The user must grant location permission."
                }
            elif status_reason == "expired":
                return {
                    "status": "error",
                    "error": "Current location snapshot has expired. An updated location observation is required."
                }
            else:
                return {
                    "status": "error",
                    "error": "Current location is currently unavailable."
                }

        return {
            "status": "success",
            "latitude": snapshot.latitude,
            "longitude": snapshot.longitude,
            "accuracy_meters": snapshot.accuracy_meters,
            "captured_at": snapshot.captured_at.isoformat(),
            "source": snapshot.source,
            "message": f"Current location observed with accuracy +/-{snapshot.accuracy_meters:.1f}m."
        }
