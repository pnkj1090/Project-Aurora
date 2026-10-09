"""
Phase 5G — Asset REST API endpoints.

Endpoints:
  POST   /api/v1/assets           — Register a temporary asset reference (metadata only)
  GET    /api/v1/assets/{id}      — Retrieve a specific temporary asset reference
  DELETE /api/v1/assets/{id}      — Immediately remove an asset reference

Rules:
- Backend generates asset_id, created_at, expires_at. Client cannot supply these.
- Active Aurora identity determines ownership. Client cannot inject user_id.
- Cross-user access → 403.
- Expired asset access → 404.
- No GET /assets list endpoint (amendment 8).
- source validated against ALLOWED_ASSET_SOURCES.
- Only PermissionStatus.AUTHORIZED allows access.
- Permission checking is routed through AssetManager → PermissionManager
  so that test overrides via get_asset_manager_dependency work correctly.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.services.identity import DEFAULT_LOCAL_USER_ID, default_identity_manager
from app.services.permissions import (
    PermissionManager,
    PermissionType,
    get_permission_manager_dependency,
)
from app.services.assets import (
    AssetManager,
    AssetReference,
    AssetType,
    get_asset_manager_dependency,
    MAX_ASSET_SIZE_BYTES,
    ALLOWED_ASSET_SOURCES,
    ALLOWED_MIME_TYPES,
)

logger = logging.getLogger("aurora.api.assets")
router = APIRouter()


# ── Request / Response models ─────────────────────────────────────────────────


class AssetIngestRequest(BaseModel):
    """
    Client-submitted asset registration request.
    Backend generates asset_id, created_at, expires_at.
    user_id is derived from active Aurora identity; any mismatched client body user_id is rejected.
    """

    asset_type: str = Field(..., description="Asset type: image, document, or file.")
    filename: str = Field(..., max_length=255, description="Sanitized display filename.")
    mime_type: str = Field(..., description="MIME type (must be from the allowed list).")
    size_bytes: int = Field(..., gt=0, le=MAX_ASSET_SIZE_BYTES, description="File size in bytes.")
    source: str = Field(..., description="Asset origin (ios_photos or ios_files).")
    user_id: Optional[str] = Field(default=None, description="Optional caller-supplied user_id for validation.")
    # Optional image dimensions
    width: Optional[int] = Field(default=None, ge=1, description="Image width (images only).")
    height: Optional[int] = Field(default=None, ge=1, description="Image height (images only).")


class AssetResponse(BaseModel):
    """Safe, serialised representation of an AssetReference."""

    asset_id: str
    asset_type: str
    filename: str
    mime_type: str
    size_bytes: int
    source: str
    user_id: str
    created_at: str
    expires_at: str
    width: Optional[int] = None
    height: Optional[int] = None

    @classmethod
    def from_ref(cls, ref: AssetReference) -> "AssetResponse":
        return cls(
            asset_id=ref.asset_id,
            asset_type=ref.asset_type.value,
            filename=ref.filename,
            mime_type=ref.mime_type,
            size_bytes=ref.size_bytes,
            source=ref.source,
            user_id=ref.user_id,
            created_at=ref.created_at.isoformat(),
            expires_at=ref.expires_at.isoformat(),
            width=ref.width,
            height=ref.height,
        )


# ── Shared dependencies ───────────────────────────────────────────────────────


def get_current_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> str:
    """Derives target identity from header or active Aurora identity."""
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()
    return default_identity_manager.get_current_user().user_id


async def require_photos_files_permission(
    user_id: str = Depends(get_current_user_id),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
) -> str:
    """
    Shared permission gate.
    Directly queries PermissionManager as the sole permission authority.
    """
    is_auth = await permission_manager.is_authorized(
        PermissionType.PHOTOS_FILES,
        user_id=user_id,
    )
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Photos/Files access is not authorized. "
                "The user must grant photos_files permission."
            ),
        )
    return user_id


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a temporary asset reference (metadata only).",
)
async def ingest_asset(
    request: AssetIngestRequest,
    user_id: str = Depends(require_photos_files_permission),
    manager: AssetManager = Depends(get_asset_manager_dependency),
):
    """
    Register a new temporary asset reference for the active user.
    Backend generates: asset_id, created_at, expires_at.
    Client cannot inject a different user_id.
    source is validated against the allowlist.
    """
    # Cross-user injection validation matching calendar & contacts pattern
    if request.user_id and request.user_id.strip() and request.user_id.strip() != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Cross-user asset injection rejected: request user_id '{request.user_id}' "
                f"does not match authenticated user '{user_id}'."
            ),
        )

    try:
        ref = await manager.ingest_asset(
            asset_type=request.asset_type,
            filename=request.filename,
            mime_type=request.mime_type,
            size_bytes=request.size_bytes,
            source=request.source,
            user_id=user_id,
            width=request.width,
            height=request.height,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return AssetResponse.from_ref(ref)


@router.get(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Retrieve a temporary asset reference by ID.",
)
async def get_asset(
    asset_id: str,
    user_id: str = Depends(require_photos_files_permission),
    manager: AssetManager = Depends(get_asset_manager_dependency),
):
    """
    Retrieve a specific temporary asset reference.
    Returns 404 if not found, expired, or belonging to a different user.
    """
    ref = await manager.get_asset(asset_id=asset_id, user_id=user_id)
    if ref is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset '{asset_id}' not found, expired, or not accessible.",
        )
    return AssetResponse.from_ref(ref)


@router.delete(
    "/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Immediately remove a temporary asset reference.",
)
async def delete_asset(
    asset_id: str,
    user_id: str = Depends(require_photos_files_permission),
    manager: AssetManager = Depends(get_asset_manager_dependency),
):
    """
    Immediately expire and remove an asset reference.
    Only the owning user may remove their own assets.
    Returns 404 if not found or cross-user.
    """
    removed = await manager.remove_asset(asset_id=asset_id, user_id=user_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset '{asset_id}' not found or not accessible.",
        )
