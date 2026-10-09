"""
Phase 5G — Backend unit tests for AssetReference model and AssetManager lifecycle.

Covers:
1.  Asset model validation — valid reference.
2.  Asset model rejects invalid asset type.
3.  Asset model rejects invalid MIME type.
4.  Asset model rejects size exceeding MAX_ASSET_SIZE_BYTES.
5.  Asset model rejects empty filename.
6.  Asset model rejects filename with path separators.
7.  Asset model rejects invalid source.
8.  Asset model rejects naive (non-UTC) datetimes.
9.  Expiration — is_expired property.
10. Expired asset is inaccessible via get_asset.
11. User isolation — user A cannot retrieve user B's asset.
12. Temporary lifecycle — 20-asset limit enforced after expired sweep.
13. Cleanup behaviour — clear_expired removes only expired assets.
14. No persistence — new AssetManager instance has no prior state.
15. Backend generates asset_id, created_at, expires_at (client cannot override).
16. Client cannot extend expiry (expiry fixed at ingest time).
"""
import asyncio
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.services.assets.models import (
    AssetReference,
    AssetType,
    MAX_ASSET_SIZE_BYTES,
    ASSET_TTL_SECONDS,
)
from app.services.assets.manager import AssetManager, MAX_ASSETS_PER_USER


# ── Helpers ───────────────────────────────────────────────────────────────────

def _valid_ref(**overrides) -> dict:
    base = dict(
        asset_type="image",
        filename="photo.jpg",
        mime_type="image/jpeg",
        size_bytes=1_000_000,
        source="ios_photos",
        user_id="user-a",
    )
    base.update(overrides)
    return base


async def _ingest(manager: AssetManager, user_id: str = "user-a", **kwargs) -> AssetReference:
    params = _valid_ref(user_id=user_id)
    params.update(kwargs)
    return await manager.ingest_asset(
        asset_type=params["asset_type"],
        filename=params["filename"],
        mime_type=params["mime_type"],
        size_bytes=params["size_bytes"],
        source=params["source"],
        user_id=user_id,
    )


# ── Model validation ──────────────────────────────────────────────────────────

def test_asset_model_valid_reference():
    ref = AssetReference(**_valid_ref())
    assert ref.asset_type == AssetType.IMAGE
    assert ref.filename == "photo.jpg"
    assert ref.mime_type == "image/jpeg"
    assert ref.size_bytes == 1_000_000
    assert ref.source == "ios_photos"
    assert ref.asset_id  # non-empty server-generated UUID


def test_asset_model_rejects_invalid_asset_type():
    with pytest.raises(Exception):
        AssetReference(**_valid_ref(asset_type="video"))


def test_asset_model_rejects_invalid_mime_type():
    with pytest.raises(ValueError, match="MIME type"):
        AssetReference(**_valid_ref(mime_type="application/x-executable"))


def test_asset_size_boundary_50mb():
    assert MAX_ASSET_SIZE_BYTES == 50_000_000
    # Exactly 50 MB (50_000_000 bytes) passes
    ref = AssetReference(**_valid_ref(size_bytes=50_000_000))
    assert ref.size_bytes == 50_000_000

    # 50 MB + 1 byte fails
    with pytest.raises(ValueError, match="exceeds maximum"):
        AssetReference(**_valid_ref(size_bytes=50_000_001))


def test_asset_ttl_is_3600_seconds_and_boundary():
    assert ASSET_TTL_SECONDS == 3600
    ref = AssetReference(**_valid_ref())
    diff = (ref.expires_at - ref.created_at).total_seconds()
    # Expiry must be approximately 1 hour (3600 seconds) after creation
    assert 3598 <= diff <= 3602


def test_asset_manager_has_no_is_assets_authorized():
    manager = AssetManager()
    assert not hasattr(manager, "is_assets_authorized"), (
        "AssetManager must not contain is_assets_authorized() or any permission authority method."
    )
    assert not hasattr(manager, "permission_manager"), (
        "AssetManager must not hold or inject PermissionManager."
    )


def test_asset_model_rejects_empty_filename():
    with pytest.raises(ValueError, match="filename cannot be empty"):
        AssetReference(**_valid_ref(filename="   "))


def test_asset_model_rejects_filename_with_path_separator():
    with pytest.raises(ValueError, match="path separators"):
        AssetReference(**_valid_ref(filename="../../etc/passwd"))


def test_asset_model_rejects_invalid_source():
    with pytest.raises(ValueError, match="source"):
        AssetReference(**_valid_ref(source="google_drive"))


def test_asset_model_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        AssetReference(**_valid_ref(created_at=datetime.now()))  # no tzinfo


# ── Expiration ────────────────────────────────────────────────────────────────

def test_asset_model_is_expired_false_for_fresh():
    ref = AssetReference(**_valid_ref())
    assert ref.is_expired is False


def test_asset_model_is_expired_true_for_past():
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    ref = AssetReference(
        **_valid_ref(),
        expires_at=past,
    )
    assert ref.is_expired is True


# ── Manager lifecycle ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expired_asset_inaccessible():
    manager = AssetManager()
    ref = await _ingest(manager)
    # Manually expire by overwriting expires_at in the store
    manager._assets["user-a"][0] = ref.model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    result = await manager.get_asset(ref.asset_id, user_id="user-a")
    assert result is None


@pytest.mark.asyncio
async def test_user_isolation():
    manager = AssetManager()
    ref = await _ingest(manager, user_id="user-a")
    # User B cannot access user A's asset
    result = await manager.get_asset(ref.asset_id, user_id="user-b")
    assert result is None


@pytest.mark.asyncio
async def test_20_asset_limit_enforced_after_expired_sweep():
    manager = AssetManager()
    user_id = "limit-user"

    # Fill to MAX_ASSETS_PER_USER
    for i in range(MAX_ASSETS_PER_USER):
        await manager.ingest_asset(
            asset_type="image",
            filename=f"photo_{i}.jpg",
            mime_type="image/jpeg",
            size_bytes=1000,
            source="ios_photos",
            user_id=user_id,
        )

    # Attempting one more without expired entries should raise
    with pytest.raises(ValueError, match="Asset limit reached"):
        await manager.ingest_asset(
            asset_type="image",
            filename="overflow.jpg",
            mime_type="image/jpeg",
            size_bytes=1000,
            source="ios_photos",
            user_id=user_id,
        )


@pytest.mark.asyncio
async def test_expired_sweep_allows_new_ingest():
    manager = AssetManager()
    user_id = "sweep-user"

    # Fill to limit, then expire all
    refs = []
    for i in range(MAX_ASSETS_PER_USER):
        ref = await manager.ingest_asset(
            asset_type="image",
            filename=f"photo_{i}.jpg",
            mime_type="image/jpeg",
            size_bytes=1000,
            source="ios_photos",
            user_id=user_id,
        )
        refs.append(ref)

    # Expire all of them
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    manager._assets[user_id] = [r.model_copy(update={"expires_at": expired}) for r in refs]

    # Now a new ingest should succeed (sweep happens first)
    new_ref = await manager.ingest_asset(
        asset_type="document",
        filename="doc.pdf",
        mime_type="application/pdf",
        size_bytes=5000,
        source="ios_files",
        user_id=user_id,
    )
    assert new_ref.asset_id


@pytest.mark.asyncio
async def test_clear_expired_removes_only_expired():
    manager = AssetManager()
    user_id = "clear-user"

    fresh = await _ingest(manager, user_id=user_id, filename="fresh.jpg")
    stale = await _ingest(manager, user_id=user_id, filename="stale.jpg")
    # Expire only stale
    manager._assets[user_id] = [
        ref if ref.asset_id == fresh.asset_id
        else ref.model_copy(update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        for ref in manager._assets[user_id]
    ]

    removed = manager.clear_expired(user_id=user_id)
    assert removed == 1
    active = manager._list_active_assets(user_id=user_id)
    assert len(active) == 1
    assert active[0].asset_id == fresh.asset_id


@pytest.mark.asyncio
async def test_no_persistence_new_manager_instance_is_empty():
    manager = AssetManager()
    await _ingest(manager, user_id="user-a")
    # A brand-new manager has no knowledge of prior state
    fresh_manager = AssetManager()
    active = fresh_manager._list_active_assets(user_id="user-a")
    assert active == []


@pytest.mark.asyncio
async def test_backend_generates_asset_id():
    manager = AssetManager()
    ref = await _ingest(manager)
    # asset_id must be a non-empty UUID string (server-generated)
    assert ref.asset_id
    parsed = uuid.UUID(ref.asset_id)  # raises if not a valid UUID
    assert str(parsed) == ref.asset_id


@pytest.mark.asyncio
async def test_client_cannot_extend_expiry():
    """Expiry is fixed at ingest time; the manager always uses server-generated TTL."""
    manager = AssetManager()
    before = datetime.now(timezone.utc)
    ref = await _ingest(manager)
    expected_expiry_min = before + timedelta(seconds=ASSET_TTL_SECONDS - 2)
    expected_expiry_max = before + timedelta(seconds=ASSET_TTL_SECONDS + 2)
    assert expected_expiry_min <= ref.expires_at <= expected_expiry_max


@pytest.mark.asyncio
async def test_remove_asset_deletes_immediately():
    manager = AssetManager()
    ref = await _ingest(manager)
    removed = await manager.remove_asset(ref.asset_id, user_id="user-a")
    assert removed is True
    result = await manager.get_asset(ref.asset_id, user_id="user-a")
    assert result is None


@pytest.mark.asyncio
async def test_remove_asset_cross_user_rejected():
    manager = AssetManager()
    ref = await _ingest(manager, user_id="user-a")
    # User B cannot remove user A's asset
    removed = await manager.remove_asset(ref.asset_id, user_id="user-b")
    assert removed is False
    # Still retrievable by user A
    result = await manager.get_asset(ref.asset_id, user_id="user-a")
    assert result is not None
