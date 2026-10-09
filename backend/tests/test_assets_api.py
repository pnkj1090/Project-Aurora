"""
Phase 5G — API integration tests for /api/v1/assets endpoints.

Uses AsyncClient + ASGI transport + dependency overrides.
Matches the contacts/calendar API test pattern exactly.

Security/privacy regression tests (amendment 10):
1.  Permission gate — unauthorized access returns 403.
2.  POST ingest — valid request creates asset reference.
3.  POST ingest — backend generates asset_id.
4.  POST ingest — backend generates created_at and expires_at.
5.  POST ingest — fabricated client user_id in body is ignored.
6.  POST ingest — invalid MIME type rejected with 400.
7.  POST ingest — invalid source rejected with 400.
8.  POST ingest — size exceeding limit rejected.
9.  GET retrieve — valid asset returned.
10. GET retrieve — expired asset returns 404.
11. GET retrieve — cross-user asset returns 404.
12. DELETE — removes asset immediately, then 404.
13. DELETE — cross-user delete returns 404.
14. No GET /assets list endpoint (amendment 8).
"""
import uuid
import pytest
from datetime import datetime, timezone, timedelta

from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.assets.manager import AssetManager, get_asset_manager_dependency
from app.services.assets.models import MAX_ASSET_SIZE_BYTES, ASSET_TTL_SECONDS
from app.services.permissions import (
    PermissionManager,
    InMemoryPermissionStore,
    PermissionType,
    PermissionStatus,
    get_permission_manager_dependency,
)


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def override_asset_manager():
    """Provide isolated AssetManager + PermissionManager, matching contacts pattern."""
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    manager = AssetManager()
    app.dependency_overrides[get_asset_manager_dependency] = lambda: manager
    app.dependency_overrides[get_permission_manager_dependency] = lambda: perm_manager
    yield manager, perm_manager
    app.dependency_overrides.pop(get_asset_manager_dependency, None)
    app.dependency_overrides.pop(get_permission_manager_dependency, None)


def _valid_payload(**overrides) -> dict:
    base = {
        "asset_type": "image",
        "filename": "test_photo.jpg",
        "mime_type": "image/jpeg",
        "size_bytes": 1_500_000,
        "source": "ios_photos",
    }
    base.update(overrides)
    return base


USER_A = "api-user-a"
USER_B = "api-user-b"


def _headers(user_id: str = USER_A) -> dict:
    return {"X-User-ID": user_id}


async def _grant(perm_manager: PermissionManager, user_id: str):
    await perm_manager.update_permission(
        PermissionType.PHOTOS_FILES, PermissionStatus.AUTHORIZED, user_id=user_id
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assets_api_unauthorized_access(override_asset_manager):
    manager, perm_manager = override_asset_manager
    # No permission granted → 403
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert resp.status_code == 403
        assert "not authorized" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_assets_api_ingest_valid(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["asset_type"] == "image"
        assert data["filename"] == "test_photo.jpg"
        assert data["mime_type"] == "image/jpeg"
        assert data["source"] == "ios_photos"
        assert data["user_id"] == USER_A


@pytest.mark.asyncio
async def test_assets_api_backend_generates_asset_id(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert resp.status_code == 201
        data = resp.json()
        parsed = uuid.UUID(data["asset_id"])
        assert str(parsed) == data["asset_id"]


@pytest.mark.asyncio
async def test_assets_api_backend_generates_timestamps(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    before = datetime.now(timezone.utc)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        after = datetime.now(timezone.utc)
        assert resp.status_code == 201
        data = resp.json()
        created_at = datetime.fromisoformat(data["created_at"])
        expires_at = datetime.fromisoformat(data["expires_at"])
        assert before <= created_at <= after
        assert expires_at > created_at
        # Expiry must be approximately 1 hour (3600 seconds) after creation
        diff = (expires_at - created_at).total_seconds()
        assert 3590 <= diff <= 3610


@pytest.mark.asyncio
async def test_assets_api_client_cannot_supply_or_extend_expires_at(override_asset_manager):
    """Client-supplied expires_at is ignored/overridden by server-side TTL of 3600s."""
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = _valid_payload()
        payload["expires_at"] = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        resp = await client.post(
            "/api/v1/assets", json=payload, headers=_headers(USER_A)
        )
        assert resp.status_code == 201
        data = resp.json()
        created_at = datetime.fromisoformat(data["created_at"])
        expires_at = datetime.fromisoformat(data["expires_at"])
        # Server must enforce ~3600 seconds TTL, not client's 365 days
        diff = (expires_at - created_at).total_seconds()
        assert 3590 <= diff <= 3610


@pytest.mark.asyncio
async def test_assets_api_cross_user_body_injection_rejected(override_asset_manager):
    """Client sending mismatched user_id in body is rejected with 403 Forbidden."""
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    payload = _valid_payload()
    payload["user_id"] = USER_B  # attempt injection
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets", json=payload, headers=_headers(USER_A)
        )
        assert resp.status_code == 403
        assert "Cross-user asset injection rejected" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_assets_api_matching_body_user_id_succeeds(override_asset_manager):
    """Client providing matching user_id in body succeeds and uses active identity."""
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    payload = _valid_payload()
    payload["user_id"] = USER_A
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets", json=payload, headers=_headers(USER_A)
        )
        assert resp.status_code == 201
        assert resp.json()["user_id"] == USER_A


@pytest.mark.asyncio
async def test_assets_api_rejects_invalid_mime_type(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets",
            json=_valid_payload(mime_type="application/x-malware"),
            headers=_headers(USER_A),
        )
        assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_assets_api_rejects_invalid_source(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/assets",
            json=_valid_payload(source="google_drive"),
            headers=_headers(USER_A),
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_assets_api_size_boundary_50mb(override_asset_manager):
    """Exactly 50 MB is accepted; 50 MB + 1 byte is rejected."""
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Boundary: exactly 50_000_000 bytes succeeds
        resp_exact = await client.post(
            "/api/v1/assets",
            json=_valid_payload(size_bytes=50_000_000),
            headers=_headers(USER_A),
        )
        assert resp_exact.status_code == 201
        assert resp_exact.json()["size_bytes"] == 50_000_000

        # Boundary: 50_000_001 bytes fails
        resp_over = await client.post(
            "/api/v1/assets",
            json=_valid_payload(size_bytes=50_000_001),
            headers=_headers(USER_A),
        )
        assert resp_over.status_code in (400, 422)


@pytest.mark.asyncio
async def test_assets_api_get_valid_asset(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert post.status_code == 201
        asset_id = post.json()["asset_id"]
        get = await client.get(f"/api/v1/assets/{asset_id}", headers=_headers(USER_A))
        assert get.status_code == 200
        assert get.json()["asset_id"] == asset_id


@pytest.mark.asyncio
async def test_assets_api_expired_asset_returns_404(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert post.status_code == 201
        asset_id = post.json()["asset_id"]
        # Manually expire the asset
        manager._assets[USER_A] = [
            ref.model_copy(update={"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)})
            for ref in manager._assets.get(USER_A, [])
        ]
        get = await client.get(f"/api/v1/assets/{asset_id}", headers=_headers(USER_A))
        assert get.status_code == 404


@pytest.mark.asyncio
async def test_assets_api_cross_user_asset_returns_404(override_asset_manager):
    """User B cannot retrieve User A's asset."""
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    await _grant(perm_manager, USER_B)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert post.status_code == 201
        asset_id = post.json()["asset_id"]
        # User B tries to access User A's asset
        get = await client.get(f"/api/v1/assets/{asset_id}", headers=_headers(USER_B))
        assert get.status_code == 404


@pytest.mark.asyncio
async def test_assets_api_delete_removes_asset(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert post.status_code == 201
        asset_id = post.json()["asset_id"]
        delete = await client.delete(f"/api/v1/assets/{asset_id}", headers=_headers(USER_A))
        assert delete.status_code == 204
        get = await client.get(f"/api/v1/assets/{asset_id}", headers=_headers(USER_A))
        assert get.status_code == 404


@pytest.mark.asyncio
async def test_assets_api_cross_user_delete_returns_404(override_asset_manager):
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    await _grant(perm_manager, USER_B)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post = await client.post(
            "/api/v1/assets", json=_valid_payload(), headers=_headers(USER_A)
        )
        assert post.status_code == 201
        asset_id = post.json()["asset_id"]
        # User B tries to delete User A's asset
        delete = await client.delete(f"/api/v1/assets/{asset_id}", headers=_headers(USER_B))
        assert delete.status_code == 404


@pytest.mark.asyncio
async def test_no_public_asset_list_endpoint(override_asset_manager):
    """GET /api/v1/assets (no ID) must not expose a listing — amendment 8."""
    manager, perm_manager = override_asset_manager
    await _grant(perm_manager, USER_A)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/assets", headers=_headers(USER_A))
        assert resp.status_code in (404, 405)
