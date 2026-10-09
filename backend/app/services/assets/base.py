"""
Abstract base interface for Aurora Asset Management (Phase 5G).

Permission checking is NOT the responsibility of AssetManager.
PermissionManager + ContextAccessPolicy remain the sole permission authorities.
AssetManager owns: ownership validation, lifecycle (expiry/limits), and CRUD.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from app.services.assets.models import AssetReference
from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class BaseAssetManager(ABC):
    """Abstract interface for temporary asset reference management."""

    @abstractmethod
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
        Register a new temporary asset reference for the active user.
        Backend generates asset_id, created_at, and expires_at.
        Client cannot supply these fields.
        Returns the created AssetReference.
        Raises ValueError on validation failure.
        Raises PermissionError if caller is not allowed (lifecycle limit exceeded).
        """

    @abstractmethod
    async def get_asset(
        self,
        asset_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[AssetReference]:
        """
        Retrieve a specific asset reference by ID for the active user.
        Returns None if the asset does not exist, is expired, or belongs to
        a different user.
        """

    @abstractmethod
    async def get_assets_by_ids(
        self,
        asset_ids: List[str],
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> List[AssetReference]:
        """
        Retrieve a bounded list of asset references by explicit IDs.
        Returns only the assets that exist, are unexpired, and belong to the user.
        Maximum of MAX_TOOL_ASSETS IDs accepted.
        """

    @abstractmethod
    async def remove_asset(
        self,
        asset_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> bool:
        """
        Immediately remove an asset reference.
        Returns True if the asset was found and removed, False otherwise.
        Only the owning user may remove their own asset.
        """

    @abstractmethod
    def clear_expired(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> int:
        """
        Remove all expired asset references for the user.
        Returns the number of entries removed.
        """

    @abstractmethod
    def _list_active_assets(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> List[AssetReference]:
        """
        Internal: Return all unexpired asset references for the user.
        NOT exposed as a public API endpoint.
        Used only for lifecycle management and tests.
        """
