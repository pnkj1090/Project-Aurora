import pytest
from datetime import datetime, timezone, timedelta

from app.services.context.base import ContextSensitivity
from app.services.context.policy import ContextAccessPolicy
from app.services.context.engine import ContextEngine
from app.services.context.providers.calendar import CalendarContextProvider
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.calendar.models import CalendarEvent
from app.services.calendar.manager import CalendarManager


@pytest.fixture
def permission_manager():
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


@pytest.fixture
def calendar_manager(permission_manager):
    return CalendarManager(permission_manager=permission_manager)


@pytest.fixture
def access_policy(permission_manager):
    return ContextAccessPolicy(permission_manager=permission_manager)


def test_calendar_context_provider_metadata():
    provider = CalendarContextProvider()
    assert provider.name == "calendar"
    assert provider.context_type == "calendar"
    assert provider.source == "ios/eventkit"
    assert provider.sensitivity == ContextSensitivity.SENSITIVE
    assert provider.required_permission == PermissionType.CALENDAR
    assert provider.priority == 28


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,should_allow",
    [
        (PermissionStatus.NOT_DETERMINED, False),
        (PermissionStatus.DENIED, False),
        (PermissionStatus.RESTRICTED, False),
        (PermissionStatus.UNAVAILABLE, False),
        (PermissionStatus.AUTHORIZED, True),
    ],
)
async def test_calendar_context_policy_enforcement(
    calendar_manager, permission_manager, access_policy, status, should_allow
):
    user_id = f"test_ctx_{status.value}"
    await permission_manager.update_permission(PermissionType.CALENDAR, status, user_id=user_id)

    provider = CalendarContextProvider(calendar_manager=calendar_manager)
    is_allowed, reason = await access_policy.is_context_allowed(provider, user_id=user_id)

    assert is_allowed is should_allow
    if not should_allow:
        assert "not authorized" in reason


@pytest.mark.asyncio
async def test_calendar_context_formatting_and_bounds(calendar_manager, permission_manager):
    user_id = "user_ctx_format"
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)

    now = datetime.now(timezone.utc)
    ev_timed = CalendarEvent(
        event_id="evt-timed",
        title="Architecture Review",
        start_at=now + timedelta(hours=2),
        end_at=now + timedelta(hours=3),
        all_day=False,
        calendar_name="Engineering",
        location="Room 404",
        user_id=user_id,
    )
    ev_allday = CalendarEvent(
        event_id="evt-allday",
        title="Hackathon Day 1",
        start_at=now,
        end_at=now + timedelta(days=1),
        all_day=True,
        calendar_name="Company",
        user_id=user_id,
    )

    await calendar_manager.set_events([ev_timed, ev_allday], user_id=user_id)

    provider = CalendarContextProvider(calendar_manager=calendar_manager, window_hours=24, max_events=5)
    block = await provider.get_context(user_id=user_id)

    assert block is not None
    assert block.sensitivity == ContextSensitivity.SENSITIVE
    assert block.required_permission == PermissionType.CALENDAR
    assert "Architecture Review" in block.content
    assert "Room 404" in block.content
    assert "Hackathon Day 1" in block.content
    assert "All-day event" in block.content


@pytest.mark.asyncio
async def test_calendar_context_engine_integration(calendar_manager, permission_manager, access_policy):
    user_id = "user_engine_integration"
    provider = CalendarContextProvider(calendar_manager=calendar_manager)
    engine = ContextEngine(providers=[provider], policy=access_policy)

    now = datetime.now(timezone.utc)
    event = CalendarEvent(
        event_id="evt-eng",
        title="Product Roadmap",
        start_at=now + timedelta(hours=1),
        end_at=now + timedelta(hours=2),
        user_id=user_id,
    )

    # 1. When unauthorized: calendar context omitted from system prompt
    prompt = await engine.build_system_prompt(user_id=user_id)
    assert "Product Roadmap" not in prompt
    assert "Upcoming Schedule" not in prompt

    # 2. When authorized: calendar context appears in system prompt
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)
    await calendar_manager.set_events([event], user_id=user_id)

    prompt_auth = await engine.build_system_prompt(user_id=user_id)
    assert "Product Roadmap" in prompt_auth
    assert "Upcoming Schedule" in prompt_auth
