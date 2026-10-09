"""
Phase 5G — AssetManager: in-memory, user-scoped, ephemeral asset reference lifecycle.

CRITICAL ARCHITECTURAL CONTRACT:
- AssetManager does NOT own permission checking.
  PermissionManager + ContextAccessPolicy are the sole permission authorities.
  AssetManager owns ONLY: ownership validation, lifecycle, limits, and CRUD.
- All asset references are temporary (in-memory only, never persisted to disk/DB).
- No binary content is ever stored.
- No filesystem scanning, no Photos library enumeration.
- No asset logging of filenames or content.
- Expired assets are inaccessible and swept before limit enforcement.
- 20-asset per-user limit is enforced after sweeping expired entries.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from app.services.assets.base import BaseAssetManager
from app.services.assets.models import (
    AssetReference,
    AssetType,
    MAX_ASSETS_PER_USER,
    MAX_ASSET_SIZE_BYTES,
    ASSET_TTL_SECONDS,
    MAX_TOOL_ASSETS,
    ALLOWED_ASSET_SOURCES,
    ALLOWED_MIME_TYPES,
)
from app.services.identity.models import DEFAULT_LOCAL_USER_ID

logger = logging.getLogger("aurora.assets.manager")


class AssetManager(BaseAssetManager):
    """
    In-memory, user-scoped, ephemeral asset reference manager.

    Lifecycle rules:
    - ingest_asset: clear expired first, then enforce 20-ref limit, then store.
    - get_asset / get_assets_by_ids: always check expiry; return None/omit if expired.
    - remove_asset: immediate deletion.
    - clear_expired: sweeps all expired entries for the user.
    - _list_active_assets: internal only — bounded, unexpired, user-scoped.

    Permission boundary:
    - AssetManager does NOT perform permission authorization.
    - PermissionManager + ContextAccessPolicy are the sole permission authorities.

    Safe logging:
    - Log asset type and user only. NEVER log filenames or file contents.
    """

    def __init__(self) -> None:
        # Ephemeral store: user_id → list of AssetReference
        self._assets: Dict[str, List[AssetReference]] = {}

    # ── Ingestion ────────────────────────────────────────────────────────────

    async def ingest_asset(
        self,
        asset_type: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        source: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> AssetReference:
        """
        Create and store a new temporary asset reference.
        Backend generates asset_id, created_at, expires_at.
        Client cannot supply these values.
        """
        # Step 1: sweep expired entries before enforcing limit
        removed = self.clear_expired(user_id=user_id)
        if removed:
            logger.debug(
                "Cleared %d expired asset(s) for user '%s' before ingestion.", removed, user_id
            )

        # Step 2: enforce per-user limit
        active = self._list_active_assets(user_id=user_id)
        if len(active) >= MAX_ASSETS_PER_USER:
            raise ValueError(
                f"Asset limit reached: user '{user_id}' already has "
                f"{len(active)} active asset reference(s) "
                f"(maximum is {MAX_ASSETS_PER_USER}). "
                f"Remove or wait for existing assets to expire."
            )

        # Step 3: validate and build AssetReference
        # Pydantic validators on AssetReference handle MIME, source, size, filename.
        now = datetime.now(timezone.utc)
        ref = AssetReference(
            asset_type=asset_type,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            source=source,
            user_id=user_id,
            created_at=now,
            expires_at=now + timedelta(seconds=ASSET_TTL_SECONDS),
            width=width,
            height=height,
        )

        # Step 4: store
        self._assets.setdefault(user_id, []).append(ref)
        # Safe logging: type only, never filename
        logger.info(
            "Ingested asset type='%s' size=%d for user='%s'.",
            ref.asset_type.value,
            ref.size_bytes,
            user_id,
        )
        return ref

    # ── Retrieval ────────────────────────────────────────────────────────────

    async def get_asset(
        self,
        asset_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[AssetReference]:
        """
        Retrieve a single asset reference if it exists, is unexpired, and
        belongs to the requesting user.
        Returns None otherwise (cross-user, expired, or missing).
        """
        for ref in self._assets.get(user_id, []):
            if ref.asset_id == asset_id:
                if ref.is_expired:
                    logger.debug("Asset '%s' is expired for user '%s'.", asset_id, user_id)
                    return None
                return ref
        return None

    async def get_assets_by_ids(
        self,
        asset_ids: List[str],
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> List[AssetReference]:
        """
        Retrieve an explicit bounded list of asset references.
        Returns only: existing + unexpired + user-owned entries.
        Silently skips missing, expired, or cross-user IDs.
        Maximum of MAX_TOOL_ASSETS IDs honoured.
        """
        capped = asset_ids[:MAX_TOOL_ASSETS]
        results: List[AssetReference] = []
        for aid in capped:
            ref = await self.get_asset(aid, user_id=user_id)
            if ref is not None:
                results.append(ref)
        return results

    # ── Removal ──────────────────────────────────────────────────────────────

    async def remove_asset(
        self,
        asset_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> bool:
        """
        Immediately remove a specific asset reference.
        Only the owning user may remove their own asset.
        Returns True if removed, False if not found or cross-user.
        """
        user_refs = self._assets.get(user_id, [])
        before = len(user_refs)
        self._assets[user_id] = [r for r in user_refs if r.asset_id != asset_id]
        removed = len(self._assets[user_id]) < before
        if removed:
            logger.info("Removed asset '%s' for user '%s'.", asset_id, user_id)
        return removed

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def clear_expired(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> int:
        """
        Remove all expired asset references for the user.
        Returns the number of entries removed.
        """
        user_refs = self._assets.get(user_id, [])
        active = [r for r in user_refs if not r.is_expired]
        removed_count = len(user_refs) - len(active)
        if removed_count:
            self._assets[user_id] = active
            logger.debug(
                "Cleared %d expired asset reference(s) for user '%s'.",
                removed_count,
                user_id,
            )
        return removed_count

    # ── Internal / Tests ─────────────────────────────────────────────────────

    def _list_active_assets(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> List[AssetReference]:
        """
        Internal: return all unexpired asset references for the user.
        NOT exposed as a public API endpoint.
        Used for lifecycle management, limit enforcement, and tests only.
        """
        return [r for r in self._assets.get(user_id, []) if not r.is_expired]


# ── Singleton ─────────────────────────────────────────────────────────────────

_default_asset_manager: Optional[AssetManager] = None


def get_asset_manager() -> AssetManager:
    """Retrieve or construct the singleton AssetManager instance."""
    global _default_asset_manager
    if _default_asset_manager is None:
        _default_asset_manager = AssetManager()
    return _default_asset_manager


def get_asset_manager_dependency() -> AssetManager:
    """FastAPI dependency provider."""
    return get_asset_manager()
