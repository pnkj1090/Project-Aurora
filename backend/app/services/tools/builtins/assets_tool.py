"""
Phase 5G — GetSelectedAssetsTool.

CRITICAL SECURITY CONTRACT (Phase 5G amendments):
1. Tool REQUIRES either a specific asset_id or an explicit asset_ids list (max 5).
   An empty-argument call is REJECTED — no "return all assets" behaviour exists.
2. Tool can only retrieve metadata, never binary content.
3. Tool cannot enumerate the user's photo library or filesystem.
4. Requires PermissionType.PHOTOS_FILES = AUTHORIZED.
5. Expired assets are silently excluded.
6. Cross-user assets are silently excluded.
7. AssetManager is the sole source of truth; no duplicate logic in tool.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.assets.manager import AssetManager, get_asset_manager, MAX_TOOL_ASSETS

logger = logging.getLogger("aurora.tools.builtins.assets")


class GetSelectedAssetsTool(BaseTool):
    """
    Tool allowing Aurora to retrieve metadata for explicitly user-selected assets.

    MANDATORY: The caller must supply either:
    - 'asset_id' (single string) — retrieve one specific asset, OR
    - 'asset_ids' (list of strings, max 5) — retrieve a bounded selection.

    An empty call (no asset_id, no asset_ids) is rejected with an error.
    This prevents the LLM from receiving an unrestricted asset dump.
    """

    def __init__(
        self,
        asset_manager: Optional[AssetManager] = None,
        permission_manager: Optional[PermissionManager] = None,
    ) -> None:
        self._manager = asset_manager
        self._permission_manager = permission_manager

    @property
    def manager(self) -> AssetManager:
        return self._manager or get_asset_manager()

    @property
    def perm_manager(self) -> PermissionManager:
        return self._permission_manager or default_permission_manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_selected_assets",
            description=(
                "Retrieve metadata (type, size, optional dimensions) for explicitly "
                "user-selected photos or files. "
                "You MUST provide either 'asset_id' (single) or 'asset_ids' (list, max 5). "
                "An empty call is not permitted. "
                "This tool returns metadata only — no binary content, no image analysis. "
                "Requires 'photos_files' permission."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "asset_id": {
                        "type": "string",
                        "description": "Retrieve a single specific asset by its ID.",
                    },
                    "asset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": MAX_TOOL_ASSETS,
                        "description": (
                            f"Retrieve up to {MAX_TOOL_ASSETS} explicitly selected assets by ID."
                        ),
                    },
                },
                "required": [],
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.PHOTOS_FILES],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or DEFAULT_LOCAL_USER_ID

        # Permission check
        is_auth = await self.perm_manager.is_authorized(
            PermissionType.PHOTOS_FILES, user_id=user_id
        )
        if not is_auth:
            logger.warning(
                "Tool 'get_selected_assets' blocked: user '%s' lacks PHOTOS_FILES permission.",
                user_id,
            )
            return {
                "status": "error",
                "error": "Action blocked: Required permission 'photos_files' is not authorized.",
            }

        # Resolve asset IDs from params
        asset_id = params.get("asset_id")
        asset_ids = params.get("asset_ids")

        ids_to_fetch: List[str] = []

        if asset_id is not None:
            if not isinstance(asset_id, str) or not asset_id.strip():
                return {
                    "status": "error",
                    "error": "Invalid arguments: 'asset_id' must be a non-empty string.",
                }
            ids_to_fetch = [asset_id.strip()]

        elif asset_ids is not None:
            if not isinstance(asset_ids, list):
                return {
                    "status": "error",
                    "error": "Invalid arguments: 'asset_ids' must be a list of strings.",
                }
            # Validate each entry
            validated: List[str] = []
            for item in asset_ids:
                if not isinstance(item, str) or not item.strip():
                    return {
                        "status": "error",
                        "error": "Invalid arguments: each entry in 'asset_ids' must be a non-empty string.",
                    }
                validated.append(item.strip())
            if not validated:
                return {
                    "status": "error",
                    "error": (
                        "Invalid arguments: 'asset_ids' list is empty. "
                        "Provide at least one asset ID."
                    ),
                }
            if len(validated) > MAX_TOOL_ASSETS:
                return {
                    "status": "error",
                    "error": (
                        f"Invalid arguments: 'asset_ids' exceeds maximum of {MAX_TOOL_ASSETS}."
                    ),
                }
            ids_to_fetch = validated

        else:
            # Neither asset_id nor asset_ids provided — reject
            return {
                "status": "error",
                "error": (
                    "Missing required argument: provide either 'asset_id' (single string) "
                    f"or 'asset_ids' (list of up to {MAX_TOOL_ASSETS} strings). "
                    "This tool cannot enumerate all assets."
                ),
            }

        # Fetch from AssetManager
        refs = await self.manager.get_assets_by_ids(ids_to_fetch, user_id=user_id)

        # Serialise metadata only — no binary content
        serialised = [
            {
                "asset_id": r.asset_id,
                "asset_type": r.asset_type.value,
                "mime_type": r.mime_type,
                "size_bytes": r.size_bytes,
                "source": r.source,
                **({"width": r.width, "height": r.height}
                   if r.asset_type.value == "image" and r.width and r.height
                   else {}),
            }
            for r in refs
        ]

        not_found = [aid for aid in ids_to_fetch if not any(r.asset_id == aid for r in refs)]

        logger.info(
            "Tool 'get_selected_assets' executed for user '%s': %d found, %d not found/expired.",
            user_id,
            len(serialised),
            len(not_found),
        )

        return {
            "status": "success",
            "count": len(serialised),
            "assets": serialised,
            **({"not_found": not_found} if not_found else {}),
        }
