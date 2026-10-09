"""
Phase 5G — AssetReference domain model.

CRITICAL PRIVACY RULES:
- AssetReference is METADATA ONLY. No binary content, no file bytes.
- No filesystem paths, no EXIF location, no hidden device metadata.
- source field must come from the server-side allowlist.
- asset_id, created_at, expires_at are always server-generated.
- Phase 5G provides no binary content to the backend or LLM.
- Future OCR/vision/media processing belongs to a later phase.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Maximum individual asset size (50 MB).
MAX_ASSET_SIZE_BYTES: int = 50_000_000

# Maximum active (unexpired) references stored per user.
MAX_ASSETS_PER_USER: int = 20

# Default time-to-live for an asset reference (1 hour).
ASSET_TTL_SECONDS: int = 3_600

# Maximum assets the context provider may expose per prompt.
MAX_CONTEXT_ASSETS: int = 5

# Maximum assets the tool may retrieve in one call.
MAX_TOOL_ASSETS: int = 5

# Allowed source values (iOS providers only).
ALLOWED_ASSET_SOURCES: frozenset[str] = frozenset({"ios_photos", "ios_files"})

# MIME type allowlist — common image and document types only.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        # Images
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/heic",
        "image/heif",
        "image/tiff",
        "image/bmp",
        # Documents
        "application/pdf",
        "text/plain",
        "text/csv",
        "application/rtf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        # Generic file
        "application/octet-stream",
    }
)


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class AssetType(str, Enum):
    """Controlled asset type classification."""
    IMAGE = "image"
    DOCUMENT = "document"
    FILE = "file"


# ──────────────────────────────────────────────────────────────────────────────
# AssetReference — server-generated, metadata-only
# ──────────────────────────────────────────────────────────────────────────────


class AssetReference(BaseModel):
    """
    Temporary, server-generated, metadata-only reference to a user-selected
    photo or file.

    PHASE 5G INVARIANTS:
    - Contains NO binary content whatsoever.
    - No filesystem path, no EXIF location data, no raw device URL.
    - asset_id / created_at / expires_at are always set by AssetManager.
    - user_id is always derived from the active Aurora identity.
    - source must be a member of ALLOWED_ASSET_SOURCES.
    - OCR, vision, and content analysis are NOT part of this model.
    """

    asset_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Server-generated unique identifier.",
    )
    asset_type: AssetType = Field(..., description="Controlled asset type.")
    filename: str = Field(..., description="Sanitized display filename.")
    mime_type: str = Field(..., description="MIME type from the ALLOWED_MIME_TYPES allowlist.")
    size_bytes: int = Field(..., gt=0, description="File size in bytes (must be > 0).")
    source: str = Field(..., description="Asset origin; must be an ALLOWED_ASSET_SOURCES member.")
    user_id: str = Field(default=DEFAULT_LOCAL_USER_ID, description="Active Aurora identity.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Server-generated creation timestamp (UTC).",
    )
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=ASSET_TTL_SECONDS),
        description="Server-generated expiry timestamp (UTC).",
    )
    # Image-only optional metadata
    width: Optional[int] = Field(default=None, description="Image width in pixels (images only).")
    height: Optional[int] = Field(default=None, description="Image height in pixels (images only).")

    # ── Validators ──────────────────────────────────────────────────────────

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("filename cannot be empty or whitespace.")
        if len(trimmed) > 255:
            raise ValueError("filename exceeds maximum length of 255 characters.")
        # Reject path separators to prevent traversal
        if "/" in trimmed or "\\" in trimmed:
            raise ValueError("filename must not contain path separators.")
        return trimmed

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, v: str) -> str:
        normalised = v.strip().lower()
        if normalised not in ALLOWED_MIME_TYPES:
            raise ValueError(
                f"MIME type '{v}' is not in the allowed list. "
                f"Permitted types: {sorted(ALLOWED_MIME_TYPES)}"
            )
        return normalised

    @field_validator("size_bytes")
    @classmethod
    def validate_size(cls, v: int) -> int:
        if v > MAX_ASSET_SIZE_BYTES:
            raise ValueError(
                f"Asset size {v} bytes exceeds maximum allowed size of "
                f"{MAX_ASSET_SIZE_BYTES} bytes ({MAX_ASSET_SIZE_BYTES // 1_000_000} MB)."
            )
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        normalised = v.strip().lower()
        if normalised not in ALLOWED_ASSET_SOURCES:
            raise ValueError(
                f"source '{v}' is not allowed. "
                f"Permitted sources: {sorted(ALLOWED_ASSET_SOURCES)}"
            )
        return normalised

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Datetime fields must be timezone-aware (UTC).")
        return v

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        """True when the asset reference has passed its expiry timestamp."""
        return datetime.now(timezone.utc) >= self.expires_at
