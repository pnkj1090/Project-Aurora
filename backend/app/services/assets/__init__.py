"""Phase 5G — Assets package public exports."""
from app.services.assets.models import (
    AssetReference,
    AssetType,
    MAX_ASSET_SIZE_BYTES,
    MAX_ASSETS_PER_USER,
    ASSET_TTL_SECONDS,
    MAX_CONTEXT_ASSETS,
    MAX_TOOL_ASSETS,
    ALLOWED_ASSET_SOURCES,
    ALLOWED_MIME_TYPES,
)
from app.services.assets.base import BaseAssetManager
from app.services.assets.manager import (
    AssetManager,
    get_asset_manager,
    get_asset_manager_dependency,
)

__all__ = [
    "AssetReference",
    "AssetType",
    "MAX_ASSET_SIZE_BYTES",
    "MAX_ASSETS_PER_USER",
    "ASSET_TTL_SECONDS",
    "MAX_CONTEXT_ASSETS",
    "MAX_TOOL_ASSETS",
    "ALLOWED_ASSET_SOURCES",
    "ALLOWED_MIME_TYPES",
    "BaseAssetManager",
    "AssetManager",
    "get_asset_manager",
    "get_asset_manager_dependency",
]
