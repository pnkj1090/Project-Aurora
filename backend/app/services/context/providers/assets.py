"""
Phase 5G — AssetContextProvider.

CRITICAL RULES (from Phase 5G amendments):
1. STRICTLY OPT-IN: returns None unless an explicit asset_query or query signal exists.
   PermissionType.PHOTOS_FILES = AUTHORIZED alone NEVER causes asset metadata to enter prompts.
2. DATA MINIMIZATION: exposes only asset type and size. Filenames are NOT exposed in generic context.
3. Bounded: MAX_CONTEXT_ASSETS = 5.
4. Permission checking is owned by PermissionManager + ContextAccessPolicy, not this provider.
5. Binary data, filesystem paths, and EXIF information are never exposed.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from app.services.context.base import BaseContextProvider, ContextBlock, ContextSensitivity
from app.services.permissions.models import PermissionType
from app.services.assets.manager import AssetManager, get_asset_manager
from app.services.assets.models import MAX_CONTEXT_ASSETS
from app.services.assets.models import AssetReference

logger = logging.getLogger("aurora.context.providers.assets")


class AssetContextProvider(BaseContextProvider):
    """
    Opt-in context provider for temporarily selected asset metadata.

    Returns None unless an explicit asset relevance signal is present in the request.
    Authorized PHOTOS_FILES permission alone does NOT trigger context injection.
    """

    def __init__(
        self,
        asset_manager: Optional[AssetManager] = None,
        max_assets: int = MAX_CONTEXT_ASSETS,
        priority: int = 40,
    ) -> None:
        self._manager = asset_manager
        self.max_assets = min(max_assets, MAX_CONTEXT_ASSETS)
        self._priority = priority

    @property
    def manager(self) -> AssetManager:
        return self._manager or get_asset_manager()

    @property
    def name(self) -> str:
        return "assets"

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def context_type(self) -> str:
        return "assets"

    @property
    def source(self) -> str:
        return "ios/photos_files"

    @property
    def sensitivity(self) -> ContextSensitivity:
        return ContextSensitivity.SENSITIVE

    @property
    def required_permission(self) -> Optional[PermissionType]:
        return PermissionType.PHOTOS_FILES

    def _resolve_user_id(self, session: Optional[Any] = None, **kwargs: Any) -> Optional[str]:
        if kwargs.get("user_id"):
            uid = str(kwargs["user_id"]).strip()
            if uid:
                return uid
        if session and hasattr(session, "user_id") and session.user_id:
            uid = str(session.user_id).strip()
            if uid:
                return uid
        if session and hasattr(session, "metadata") and isinstance(session.metadata, dict):
            uid = str(session.metadata.get("user_id") or "").strip()
            if uid:
                return uid
        return None

    async def get_context(
        self,
        session: Optional[Any] = None,
        **kwargs: Any,
    ) -> Optional[ContextBlock]:
        """
        Produce a bounded context block for currently selected assets.

        Requires an explicit asset relevance signal
        (kwargs['asset_query'] or kwargs['query']). Without it, returns None.

        Exposes type + size only. Filenames are NEVER exposed in generic context.
        """
        # Explicit relevance signal required
        explicit_query = kwargs.get("asset_query") or kwargs.get("query")
        if not explicit_query or not str(explicit_query).strip():
            logger.debug("AssetContextProvider skipped: no explicit asset relevance signal.")
            return None

        user_id = self._resolve_user_id(session, **kwargs)
        if not user_id:
            logger.warning("AssetContextProvider: No server-controlled user identity found; failing closed.")
            return None

        # Retrieve unexpired assets — permission already verified by ContextAccessPolicy
        assets = self.manager._list_active_assets(user_id=user_id)
        if not assets:
            return None

        # Bounded
        bounded: List[AssetReference] = assets[: self.max_assets]

        # Data minimization: type + size only, no filenames in generic context
        lines: List[str] = [f"Selected Assets ({len(bounded)}):"]
        for ref in bounded:
            size_mb = ref.size_bytes / 1_000_000
            line = f"- {ref.asset_type.value}, {size_mb:.1f} MB"
            if ref.asset_type.value == "image" and ref.width and ref.height:
                line += f" ({ref.width}×{ref.height})"
            lines.append(line)

        content = "\n".join(lines)

        return ContextBlock(
            title="Selected Assets",
            content=content,
            priority=self.priority,
            sensitivity=self.sensitivity,
            source=self.source,
            is_user_visible=True,
            required_permission=self.required_permission,
            metadata={"count": len(bounded)},
        )
