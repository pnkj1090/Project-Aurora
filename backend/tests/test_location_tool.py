import pytest
from datetime import datetime, timezone, timedelta

from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.location.models import LocationSnapshot
from app.services.location.manager import LocationManager
from app.services.tools.base import ToolRiskLevel
from app.services.tools.schemas import ToolCall
from app.services.tools.registry import ToolRegistry, default_tool_registry
from app.services.tools.executor import ToolExecutor
from app.services.tools.builtins.location_tool import GetCurrentLocationTool


def test_default_tool_registry_includes_location_tool():
    tools = default_tool_registry.list_tools()
    names = {t.definition.name for t in tools}
    assert "get_current_location" in names


def test_get_current_location_tool_definition():
    tool = GetCurrentLocationTool()
    assert tool.definition.name == "get_current_location"
    assert tool.definition.risk_level == ToolRiskLevel.LOW
    assert tool.definition.requires_confirmation is False
    assert PermissionType.LOCATION in tool.definition.required_permissions


@pytest.mark.asyncio
async def test_get_current_location_tool_execution_flow():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    loc_manager = LocationManager(permission_manager=perm_manager)

    registry = ToolRegistry()
    registry.register(GetCurrentLocationTool(location_manager=loc_manager))
    executor = ToolExecutor(registry=registry, permission_manager=perm_manager)

    user_id = "usr_tool_loc_test"
    call = ToolCall(id="call_loc", name="get_current_location", arguments={"_user_id": user_id})

    # 1. Blocked when permission NOT_DETERMINED
    res_blocked = await executor.execute(call, user_id=user_id)
    assert res_blocked.success is False
    assert "not authorized" in (res_blocked.error or "")

    # 2. Blocked when permission DENIED
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.DENIED,
        user_id=user_id
    )
    res_denied = await executor.execute(call, user_id=user_id)
    assert res_denied.success is False
    assert "denied" in (res_denied.error or "")

    # 3. Authorized, but no snapshot available
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_id
    )
    res_no_snap = await executor.execute(call, user_id=user_id)
    assert res_no_snap.success is True
    assert res_no_snap.output["status"] == "error"
    assert "unavailable" in res_no_snap.output["error"]

    # 4. Authorized with fresh snapshot
    now = datetime.now(timezone.utc)
    snap = LocationSnapshot(
        latitude=40.7128,
        longitude=-74.0060,
        accuracy_meters=8.5,
        captured_at=now,
        source="gps",
        user_id=user_id
    )
    loc_manager.set_current_location(snap, user_id=user_id)

    res_success = await executor.execute(call, user_id=user_id)
    assert res_success.success is True
    assert res_success.output["status"] == "success"
    assert res_success.output["latitude"] == 40.7128
    assert res_success.output["longitude"] == -74.0060
    assert res_success.output["accuracy_meters"] == 8.5
