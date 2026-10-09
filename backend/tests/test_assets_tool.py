"""
Phase 5G — Tool tests for GetSelectedAssetsTool.

Security/privacy regression tests (amendment 10):
1.  Tool registration in default registry.
2.  Definition has LOW risk_level.
3.  Definition requires no confirmation.
4.  Definition requires PHOTOS_FILES permission.
5.  Tool blocked when permission is NOT_DETERMINED / DENIED.
6.  Tool with no asset_id and no asset_ids → rejected (no enumeration).
7.  Tool with empty asset_ids list → rejected.
8.  Tool with asset_ids exceeding MAX_TOOL_ASSETS → rejected.
9.  Tool with valid asset_id returns metadata only (no binary content).
10. Expired asset is silently excluded from results.
11. Cross-user asset is silently excluded from results.
12. Tool cannot enumerate all assets (no "get everything" path).
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.services.assets.manager import AssetManager
from app.services.assets.models import AssetType, MAX_TOOL_ASSETS
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.permissions.models import PermissionType, PermissionStatus, PermissionRecord
from app.services.tools.builtins.assets_tool import GetSelectedAssetsTool
from app.services.tools.registry import create_default_tool_registry


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_perm_manager(status: PermissionStatus = PermissionStatus.AUTHORIZED) -> PermissionManager:
    store = InMemoryPermissionStore()
    asyncio_loop = None  # handled by pytest-asyncio
    return PermissionManager(store=store), store


async def _authorize(store: InMemoryPermissionStore, user_id: str = "user-a"):
    await store.set_permission(PermissionRecord(
        permission_type=PermissionType.PHOTOS_FILES,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_id,
    ))


async def _deny(store: InMemoryPermissionStore, user_id: str = "user-a"):
    await store.set_permission(PermissionRecord(
        permission_type=PermissionType.PHOTOS_FILES,
        status=PermissionStatus.DENIED,
        user_id=user_id,
    ))


async def _ingest_asset(manager: AssetManager, user_id: str = "user-a") -> str:
    ref = await manager.ingest_asset(
        asset_type="image",
        filename="photo.jpg",
        mime_type="image/jpeg",
        size_bytes=2_000_000,
        source="ios_photos",
        user_id=user_id,
    )
    return ref.asset_id


# ── Registration ──────────────────────────────────────────────────────────────

def test_default_registry_includes_asset_tool():
    registry = create_default_tool_registry()
    tool = registry.get("get_selected_assets")
    assert tool is not None


def test_asset_tool_definition_risk_and_confirmation():
    tool = GetSelectedAssetsTool()
    defn = tool.definition
    from app.services.tools.base import ToolRiskLevel
    assert defn.risk_level == ToolRiskLevel.LOW
    assert defn.requires_confirmation is False


def test_asset_tool_requires_photos_files_permission():
    tool = GetSelectedAssetsTool()
    assert PermissionType.PHOTOS_FILES in tool.definition.required_permissions


# ── Permission blocking ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_blocked_when_not_determined():
    pm, store = _make_perm_manager()
    # Default = NOT_DETERMINED, no explicit grant
    manager = AssetManager()
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    result = await tool.execute({"asset_id": "some-id", "_user_id": "user-a"})
    assert result["status"] == "error"
    assert "not authorized" in result["error"].lower()


@pytest.mark.asyncio
async def test_tool_blocked_when_denied():
    pm, store = _make_perm_manager()
    await _deny(store)
    manager = AssetManager()
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    result = await tool.execute({"asset_id": "some-id", "_user_id": "user-a"})
    assert result["status"] == "error"
    assert "not authorized" in result["error"].lower()


# ── Empty-call rejection ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_rejects_empty_call_no_enumeration():
    """Amendment 1 & 10: empty call must be rejected; no 'return all' path."""
    pm, store = _make_perm_manager()
    await _authorize(store)
    manager = AssetManager()
    await _ingest_asset(manager)
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    # No asset_id, no asset_ids
    result = await tool.execute({"_user_id": "user-a"})
    assert result["status"] == "error"
    assert "cannot enumerate" in result["error"].lower() or "missing" in result["error"].lower()


@pytest.mark.asyncio
async def test_tool_rejects_empty_asset_ids_list():
    pm, store = _make_perm_manager()
    await _authorize(store)
    manager = AssetManager()
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    result = await tool.execute({"asset_ids": [], "_user_id": "user-a"})
    assert result["status"] == "error"
    assert "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_tool_rejects_asset_ids_exceeding_max():
    pm, store = _make_perm_manager()
    await _authorize(store)
    manager = AssetManager()
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    too_many = [f"id-{i}" for i in range(MAX_TOOL_ASSETS + 1)]
    result = await tool.execute({"asset_ids": too_many, "_user_id": "user-a"})
    assert result["status"] == "error"
    assert "exceeds" in result["error"].lower()


# ── Successful retrieval ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_returns_metadata_only_no_binary():
    pm, store = _make_perm_manager()
    await _authorize(store)
    manager = AssetManager()
    asset_id = await _ingest_asset(manager)
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    result = await tool.execute({"asset_id": asset_id, "_user_id": "user-a"})
    assert result["status"] == "success"
    assert result["count"] == 1
    asset_data = result["assets"][0]
    # Metadata fields only
    assert "asset_id" in asset_data
    assert "asset_type" in asset_data
    assert "size_bytes" in asset_data
    assert "mime_type" in asset_data
    # No binary content
    assert "content" not in asset_data
    assert "data" not in asset_data
    assert "bytes" not in asset_data


@pytest.mark.asyncio
async def test_tool_excludes_expired_assets_silently():
    pm, store = _make_perm_manager()
    await _authorize(store)
    manager = AssetManager()
    asset_id = await _ingest_asset(manager)
    # Expire it
    manager._assets["user-a"][0] = manager._assets["user-a"][0].model_copy(
        update={"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}
    )
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    result = await tool.execute({"asset_id": asset_id, "_user_id": "user-a"})
    assert result["status"] == "success"
    assert result["count"] == 0
    assert asset_id in result.get("not_found", [])


@pytest.mark.asyncio
async def test_tool_excludes_cross_user_assets():
    pm, store = _make_perm_manager()
    await _authorize(store, user_id="user-a")
    await _authorize(store, user_id="user-b")
    manager = AssetManager()
    # Ingest for user-a
    asset_id = await _ingest_asset(manager, user_id="user-a")
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    # User-b tries to access user-a's asset
    result = await tool.execute({"asset_id": asset_id, "_user_id": "user-b"})
    assert result["status"] == "success"
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_tool_cannot_enumerate_all_assets():
    """No path in the tool allows the LLM to receive all assets."""
    pm, store = _make_perm_manager()
    await _authorize(store)
    manager = AssetManager()
    # Ingest 3 assets
    for _ in range(3):
        await _ingest_asset(manager)
    tool = GetSelectedAssetsTool(asset_manager=manager, permission_manager=pm)
    # Confirm no attribute allows full enumeration
    assert not hasattr(tool, "list_all_assets")
    assert not hasattr(tool, "get_all_assets")
    # Confirm empty call is rejected
    result = await tool.execute({"_user_id": "user-a"})
    assert result["status"] == "error"


import asyncio  # needed for _make_perm_manager
