import pytest

from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.tools.base import ToolRiskLevel
from app.services.tools.registry import default_tool_registry, ToolRegistry
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ToolCall
from app.services.tools.builtins.contacts_tool import GetContactsTool
from app.services.contacts.models import ContactSnapshot
from app.services.contacts.manager import ContactManager


@pytest.fixture
def permission_manager():
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


@pytest.fixture
def contact_manager(permission_manager):
    return ContactManager(permission_manager=permission_manager)


def test_default_tool_registry_includes_contacts_tool():
    tools = default_tool_registry.list_tools()
    names = {t.definition.name for t in tools}
    assert "get_contacts" in names


def test_get_contacts_tool_definition():
    tool = GetContactsTool()
    defn = tool.definition
    assert defn.name == "get_contacts"
    assert defn.risk_level == ToolRiskLevel.LOW
    assert defn.requires_confirmation is False
    assert PermissionType.CONTACTS in defn.required_permissions


@pytest.mark.asyncio
async def test_get_contacts_tool_blocked_when_unauthorized(contact_manager, permission_manager):
    tool = GetContactsTool(contact_manager=contact_manager)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry=registry, permission_manager=permission_manager)

    user_id = "user_tool_unauth"
    call = ToolCall(
        id="call-contacts-1",
        name="get_contacts",
        arguments={"query": "Alice", "_user_id": user_id}
    )

    # 1. NOT_DETERMINED -> blocked
    res = await executor.execute(call, user_id=user_id)
    assert res.success is False
    assert "not authorized" in (res.error or "")

    # 2. DENIED -> blocked
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.DENIED, user_id=user_id)
    res_denied = await executor.execute(call, user_id=user_id)
    assert res_denied.success is False
    assert "not authorized" in (res_denied.error or "")


@pytest.mark.asyncio
async def test_get_contacts_tool_authorized_execution(contact_manager, permission_manager):
    tool = GetContactsTool(contact_manager=contact_manager)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry=registry, permission_manager=permission_manager)

    user_id = "user_tool_auth"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    # Ingest contacts
    c1 = ContactSnapshot(
        contact_id="cnt-1",
        display_name="Sarah Connor",
        phone_numbers=["+15551234"],
        email_addresses=["sarah@resistance.org"],
        organization="Resistance"
    )
    c2 = ContactSnapshot(
        contact_id="cnt-2",
        display_name="John Connor",
        phone_numbers=["+15555678"],
        email_addresses=["john@resistance.org"],
        organization="Resistance"
    )
    await contact_manager.set_contacts([c1, c2], user_id=user_id)

    # 1. Query for "Sarah"
    call_sarah = ToolCall(
        id="call-sarah",
        name="get_contacts",
        arguments={"query": "Sarah", "_user_id": user_id}
    )
    res_sarah = await executor.execute(call_sarah, user_id=user_id)
    assert res_sarah.success is True
    output = res_sarah.output
    assert output["status"] == "success"
    assert output["count"] == 1
    assert output["contacts"][0]["display_name"] == "Sarah Connor"
    assert output["contacts"][0]["phone_numbers"] == ["+15551234"]
    assert output["contacts"][0]["email_addresses"] == ["sarah@resistance.org"]
    assert output["contacts"][0]["organization"] == "Resistance"

    # 2. Query with no matches -> returns empty list gracefully
    call_empty = ToolCall(
        id="call-empty",
        name="get_contacts",
        arguments={"query": "Terminator", "_user_id": user_id}
    )
    res_empty = await executor.execute(call_empty, user_id=user_id)
    assert res_empty.success is True
    assert res_empty.output["count"] == 0
    assert res_empty.output["contacts"] == []

    # 3. Query with no search string -> returns all bounded contacts
    call_all = ToolCall(
        id="call-all",
        name="get_contacts",
        arguments={"_user_id": user_id}
    )
    res_all = await executor.execute(call_all, user_id=user_id)
    assert res_all.success is True
    assert res_all.output["count"] == 2


@pytest.mark.asyncio
async def test_get_contacts_tool_parameter_validation(contact_manager, permission_manager):
    tool = GetContactsTool(contact_manager=contact_manager)
    user_id = "user_tool_validation"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    # Invalid max_results (< 1)
    res_invalid = await tool.execute({"max_results": 0, "user_id": user_id})
    assert res_invalid["status"] == "error"
    assert "Invalid arguments" in res_invalid["error"]

    # Overly long query (> 100 chars)
    res_long = await tool.execute({"query": "x" * 105, "user_id": user_id})
    assert res_long["status"] == "error"
    assert "Invalid arguments" in res_long["error"]
