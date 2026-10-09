import pytest
from datetime import datetime, timezone, timedelta

from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.reminders.manager import ReminderManager
from app.services.reminders.store import InMemoryReminderStore
from app.services.reminders.models import ReminderStatus
from app.services.tools.base import ToolRiskLevel
from app.services.tools.schemas import ToolCall
from app.services.tools.registry import ToolRegistry, default_tool_registry
from app.services.tools.executor import ToolExecutor, ToolConfirmationRequired
from app.services.tools.builtins.reminder_tool import (
    CreateReminderTool,
    ListRemindersTool,
    CancelReminderTool,
)


def test_default_tool_registry_includes_reminder_tools():
    tools = default_tool_registry.list_tools()
    names = {t.definition.name for t in tools}

    assert "create_reminder" in names
    assert "list_reminders" in names
    assert "cancel_reminder" in names


@pytest.mark.asyncio
async def test_reminder_tool_definitions():
    create_tool = CreateReminderTool()
    list_tool = ListRemindersTool()
    cancel_tool = CancelReminderTool()

    # create_reminder
    assert create_tool.definition.name == "create_reminder"
    assert create_tool.definition.risk_level == ToolRiskLevel.MEDIUM
    assert create_tool.definition.requires_confirmation is True
    assert PermissionType.NOTIFICATIONS in create_tool.definition.required_permissions

    # list_reminders
    assert list_tool.definition.name == "list_reminders"
    assert list_tool.definition.risk_level == ToolRiskLevel.LOW
    assert list_tool.definition.requires_confirmation is False

    # cancel_reminder
    assert cancel_tool.definition.name == "cancel_reminder"
    assert cancel_tool.definition.risk_level == ToolRiskLevel.MEDIUM
    assert cancel_tool.definition.requires_confirmation is True


@pytest.mark.asyncio
async def test_create_reminder_blocked_when_permission_not_authorized():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)

    rem_store = InMemoryReminderStore()
    await rem_store.initialize()
    rem_manager = ReminderManager(store=rem_store)

    registry = ToolRegistry()
    registry.register(CreateReminderTool(reminder_manager=rem_manager))

    executor = ToolExecutor(registry=registry, permission_manager=perm_manager)

    user_id = "usr_perm_test"
    target_time = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    call = ToolCall(
        id="call_1",
        name="create_reminder",
        arguments={
            "title": "Drink water",
            "scheduled_at": target_time,
            "_user_id": user_id,
        }
    )

    # 1. Default status is NOT_DETERMINED -> blocked
    result = await executor.execute(call, is_pre_approved=True, user_id=user_id)
    assert result.success is False
    assert "not authorized" in (result.error or "")
    assert "not_determined" in (result.error or "")

    # 2. Explicitly DENIED -> blocked
    await perm_manager.update_permission(
        permission_type=PermissionType.NOTIFICATIONS,
        status=PermissionStatus.DENIED,
        user_id=user_id,
    )
    result_denied = await executor.execute(call, is_pre_approved=True, user_id=user_id)
    assert result_denied.success is False
    assert "denied" in (result_denied.error or "")

    # 3. Explicitly AUTHORIZED -> permitted
    await perm_manager.update_permission(
        permission_type=PermissionType.NOTIFICATIONS,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_id,
    )
    result_auth = await executor.execute(call, is_pre_approved=True, user_id=user_id)
    assert result_auth.success is True
    assert result_auth.output["status"] == "success"
    assert result_auth.output["title"] == "Drink water"


@pytest.mark.asyncio
async def test_create_reminder_requires_confirmation_flow():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)

    rem_store = InMemoryReminderStore()
    await rem_store.initialize()
    rem_manager = ReminderManager(store=rem_store)

    registry = ToolRegistry()
    registry.register(CreateReminderTool(reminder_manager=rem_manager))

    executor = ToolExecutor(registry=registry, permission_manager=perm_manager)

    user_id = "usr_confirm_test"
    await perm_manager.update_permission(
        permission_type=PermissionType.NOTIFICATIONS,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_id,
    )

    target_time = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    call = ToolCall(
        id="call_confirm",
        name="create_reminder",
        arguments={
            "title": "Call Bob",
            "scheduled_at": target_time,
            "_user_id": user_id,
        }
    )

    # Calling without pre-approval raises ToolConfirmationRequired
    with pytest.raises(ToolConfirmationRequired) as exc_info:
        await executor.execute(call, is_pre_approved=False, user_id=user_id)

    assert exc_info.value.pending.risk_level == ToolRiskLevel.MEDIUM
    assert "Call Bob" in exc_info.value.pending.description


@pytest.mark.asyncio
async def test_create_reminder_no_natural_language_date_guessing():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)

    rem_store = InMemoryReminderStore()
    await rem_store.initialize()
    rem_manager = ReminderManager(store=rem_store)

    create_tool = CreateReminderTool(reminder_manager=rem_manager)

    # Missing scheduled_at
    res1 = await create_tool.execute({
        "title": "Dinner",
        "scheduled_at": "",
    })
    assert res1["status"] == "error"
    assert "explicit ISO 8601" in res1["error"]

    # Natural language string like "tomorrow at 9" should NOT be guessed or silently converted
    res2 = await create_tool.execute({
        "title": "Breakfast",
        "scheduled_at": "tomorrow at 9",
    })
    assert res2["status"] == "error"
    assert "Invalid ISO datetime format" in res2["error"]


@pytest.mark.asyncio
async def test_list_and_cancel_reminder_tools():
    rem_store = InMemoryReminderStore()
    await rem_store.initialize()
    rem_manager = ReminderManager(store=rem_store)

    user_id = "usr_tools_test"
    target_time = datetime.now(timezone.utc) + timedelta(hours=4)

    rem = await rem_manager.create_reminder(
        title="Weekly Sync",
        scheduled_at=target_time,
        user_id=user_id,
    )

    list_tool = ListRemindersTool(reminder_manager=rem_manager)
    list_res = await list_tool.execute({"_user_id": user_id})
    assert list_res["status"] == "success"
    assert list_res["count"] == 1
    assert list_res["reminders"][0]["title"] == "Weekly Sync"

    cancel_tool = CancelReminderTool(reminder_manager=rem_manager)
    cancel_res = await cancel_tool.execute({"reminder_id": rem.id, "_user_id": user_id})
    assert cancel_res["status"] == "success"
    assert cancel_res["status_value"] == ReminderStatus.CANCELLED.value
