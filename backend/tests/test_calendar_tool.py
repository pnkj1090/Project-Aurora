import pytest
from datetime import datetime, timezone, timedelta

from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.tools.base import ToolRiskLevel
from app.services.tools.registry import default_tool_registry
from app.services.tools.builtins.calendar_tool import GetUpcomingCalendarEventsTool
from app.services.calendar.models import CalendarEvent
from app.services.calendar.manager import CalendarManager


@pytest.fixture
def permission_manager():
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


@pytest.fixture
def calendar_manager(permission_manager):
    return CalendarManager(permission_manager=permission_manager)


def test_calendar_tool_metadata():
    tool = GetUpcomingCalendarEventsTool()
    defn = tool.definition
    assert defn.name == "get_upcoming_calendar_events"
    assert defn.risk_level == ToolRiskLevel.LOW
    assert defn.requires_confirmation is False
    assert defn.required_permissions == [PermissionType.CALENDAR]


def test_calendar_tool_registered_in_default_registry():
    tool = default_tool_registry.get("get_upcoming_calendar_events")
    assert tool is not None
    assert isinstance(tool, GetUpcomingCalendarEventsTool)


@pytest.mark.asyncio
async def test_calendar_tool_missing_or_invalid_params(calendar_manager):
    tool = GetUpcomingCalendarEventsTool(calendar_manager=calendar_manager)

    # 1. Missing parameters
    res1 = await tool.execute({})
    assert res1["status"] == "error"
    assert "required" in res1["error"]

    # 2. Naive datetime
    res2 = await tool.execute({
        "start_at": "2026-09-22T10:00:00",  # No timezone
        "end_at": "2026-09-22T12:00:00Z"
    })
    assert res2["status"] == "error"
    assert "timezone-aware" in res2["error"]


@pytest.mark.asyncio
async def test_calendar_tool_permission_blocking(calendar_manager, permission_manager):
    user_id = "user_tool_blocked"
    tool = GetUpcomingCalendarEventsTool(calendar_manager=calendar_manager)

    now = datetime.now(timezone.utc)
    res = await tool.execute({
        "_user_id": user_id,
        "start_at": now.isoformat(),
        "end_at": (now + timedelta(hours=4)).isoformat()
    })
    assert res["status"] == "error"
    assert "not authorized" in res["error"]


@pytest.mark.asyncio
async def test_calendar_tool_authorized_execution(calendar_manager, permission_manager):
    user_id = "user_tool_auth"
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)

    now = datetime.now(timezone.utc)
    event = CalendarEvent(
        event_id="evt-tool-1",
        title="Client Demo",
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        all_day=False,
        location="Zoom link in invite",
        user_id=user_id,
    )
    await calendar_manager.set_events([event], user_id=user_id)

    tool = GetUpcomingCalendarEventsTool(calendar_manager=calendar_manager)
    res = await tool.execute({
        "_user_id": user_id,
        "start_at": now.isoformat(),
        "end_at": (now + timedelta(hours=3)).isoformat(),
        "max_results": 5
    })

    assert res["status"] == "success"
    assert res["count"] == 1
    assert res["events"][0]["title"] == "Client Demo"
    assert res["events"][0]["event_id"] == "evt-tool-1"


@pytest.mark.asyncio
async def test_calendar_tool_empty_results(calendar_manager, permission_manager):
    user_id = "user_tool_empty"
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)

    now = datetime.now(timezone.utc)
    tool = GetUpcomingCalendarEventsTool(calendar_manager=calendar_manager)
    res = await tool.execute({
        "_user_id": user_id,
        "start_at": now.isoformat(),
        "end_at": (now + timedelta(hours=1)).isoformat()
    })

    assert res["status"] == "success"
    assert res["count"] == 0
    assert res["events"] == []
