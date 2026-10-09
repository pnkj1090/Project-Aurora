import pytest
from datetime import datetime, timezone, timedelta

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.calendar.models import CalendarEvent
from app.services.calendar.manager import (
    CalendarManager,
    MAX_SNAPSHOT_EVENTS,
    MAX_QUERY_RANGE_DAYS,
)


@pytest.fixture
def permission_manager():
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


@pytest.fixture
def calendar_manager(permission_manager):
    return CalendarManager(permission_manager=permission_manager)


def test_calendar_event_model_validation():
    now = datetime.now(timezone.utc)
    ev = CalendarEvent(
        event_id="evt-1",
        title="Weekly Sync",
        start_at=now,
        end_at=now + timedelta(hours=1),
        all_day=False,
        calendar_name="Work",
        location="Meeting Room A",
        notes_available=True,
    )
    assert ev.event_id == "evt-1"
    assert ev.title == "Weekly Sync"
    assert ev.all_day is False
    assert ev.location == "Meeting Room A"
    assert ev.notes_available is True
    assert ev.source == "ios_eventkit"
    assert ev.user_id == DEFAULT_LOCAL_USER_ID


def test_calendar_event_rejects_naive_datetime():
    with pytest.raises(ValueError, match="naive datetimes are not permitted"):
        CalendarEvent(
            event_id="evt-naive",
            title="Naive Event",
            start_at=datetime(2026, 9, 22, 10, 0, 0),  # Naive
            end_at=datetime(2026, 9, 22, 11, 0, 0, tzinfo=timezone.utc),
        )


def test_calendar_event_rejects_end_before_start():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="end_at cannot be earlier than start_at"):
        CalendarEvent(
            event_id="evt-invalid-range",
            title="Backwards Event",
            start_at=now,
            end_at=now - timedelta(minutes=15),
        )


def test_calendar_event_rejects_empty_title_or_id():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="title cannot be empty"):
        CalendarEvent(
            event_id="evt-1",
            title="   ",
            start_at=now,
            end_at=now + timedelta(hours=1),
        )

    with pytest.raises(ValueError, match="event_id cannot be empty"):
        CalendarEvent(
            event_id="   ",
            title="Valid Title",
            start_at=now,
            end_at=now + timedelta(hours=1),
        )


def test_calendar_event_non_utc_timezone_handling():
    paris_tz = timezone(timedelta(hours=2))
    ny_tz = timezone(timedelta(hours=-5))

    start_paris = datetime(2026, 9, 22, 14, 0, tzinfo=paris_tz)
    end_ny = datetime(2026, 9, 22, 10, 0, tzinfo=ny_tz)  # 10:00 NY = 17:00 Paris (3 hrs later)

    ev = CalendarEvent(
        event_id="evt-tz",
        title="Cross-Timezone Call",
        start_at=start_paris,
        end_at=end_ny,
    )
    assert ev.start_at.tzinfo == paris_tz
    assert ev.end_at.tzinfo == ny_tz
    assert ev.end_at.astimezone(timezone.utc) > ev.start_at.astimezone(timezone.utc)


def test_calendar_event_all_day_representation():
    now = datetime.now(timezone.utc)
    ev = CalendarEvent(
        event_id="evt-all-day",
        title="Company Holiday",
        start_at=now,
        end_at=now + timedelta(days=1),
        all_day=True,
    )
    assert ev.all_day is True


@pytest.mark.asyncio
async def test_calendar_manager_permission_enforcement(calendar_manager, permission_manager):
    user_id = "test_user_perm"
    now = datetime.now(timezone.utc)
    event = CalendarEvent(
        event_id="evt-perm",
        title="Secret Board Meeting",
        start_at=now,
        end_at=now + timedelta(hours=1),
        user_id=user_id,
    )

    # 1. Blocked when NOT_DETERMINED
    with pytest.raises(PermissionError, match="Calendar access is not authorized"):
        await calendar_manager.set_events([event], user_id=user_id)

    events, reason = await calendar_manager.get_events(
        start_at=now, end_at=now + timedelta(hours=2), user_id=user_id
    )
    assert events is None
    assert reason == "unauthorized"

    # 2. Blocked when DENIED
    await permission_manager.update_permission(
        PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=user_id
    )
    with pytest.raises(PermissionError):
        await calendar_manager.set_events([event], user_id=user_id)

    # 3. Allowed when AUTHORIZED
    await permission_manager.update_permission(
        PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id
    )
    saved = await calendar_manager.set_events([event], user_id=user_id)
    assert len(saved) == 1

    queried, reason = await calendar_manager.get_events(
        start_at=now, end_at=now + timedelta(hours=2), user_id=user_id
    )
    assert reason == "available"
    assert len(queried) == 1
    assert queried[0].event_id == "evt-perm"


@pytest.mark.asyncio
async def test_calendar_manager_cross_user_injection_rejected(calendar_manager, permission_manager):
    user_a = "user_alpha"
    user_b = "user_beta"

    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_a)
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_b)

    now = datetime.now(timezone.utc)
    event_b = CalendarEvent(
        event_id="evt-cross",
        title="Injected Meeting",
        start_at=now,
        end_at=now + timedelta(hours=1),
        user_id=user_b,  # Explicitly claiming user_b
    )

    # User A attempting to ingest user B's event must be rejected
    with pytest.raises(ValueError, match="Cross-user calendar injection rejected"):
        await calendar_manager.set_events([event_b], user_id=user_a)


@pytest.mark.asyncio
async def test_calendar_manager_duplicate_event_id_rejected(calendar_manager, permission_manager):
    user_id = "user_dups"
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)

    now = datetime.now(timezone.utc)
    ev1 = CalendarEvent(event_id="evt-dup", title="Meeting 1", start_at=now, end_at=now + timedelta(hours=1), user_id=user_id)
    ev2 = CalendarEvent(event_id="evt-dup", title="Meeting 2", start_at=now + timedelta(hours=2), end_at=now + timedelta(hours=3), user_id=user_id)

    with pytest.raises(ValueError, match="Duplicate event ID 'evt-dup' detected"):
        await calendar_manager.set_events([ev1, ev2], user_id=user_id)


@pytest.mark.asyncio
async def test_calendar_manager_snapshot_replacement_and_ordering(calendar_manager, permission_manager):
    user_id = "user_ordering"
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)

    now = datetime.now(timezone.utc)
    ev1 = CalendarEvent(event_id="evt-later", title="Late Sync", start_at=now + timedelta(hours=5), end_at=now + timedelta(hours=6), user_id=user_id)
    ev2 = CalendarEvent(event_id="evt-earlier", title="Early Standup", start_at=now + timedelta(hours=1), end_at=now + timedelta(hours=2), user_id=user_id)
    ev3 = CalendarEvent(event_id="evt-tie-a", title="Tie A", start_at=now + timedelta(hours=3), end_at=now + timedelta(hours=4), user_id=user_id)
    ev4 = CalendarEvent(event_id="evt-tie-b", title="Tie B", start_at=now + timedelta(hours=3), end_at=now + timedelta(hours=4), user_id=user_id)

    await calendar_manager.set_events([ev1, ev2, ev4, ev3], user_id=user_id)

    # Query chronological ordering
    queried, _ = await calendar_manager.get_events(
        start_at=now, end_at=now + timedelta(hours=10), user_id=user_id
    )
    assert len(queried) == 4
    # Check deterministic order: earlier -> tie-a -> tie-b -> later
    assert queried[0].event_id == "evt-earlier"
    assert queried[1].event_id == "evt-tie-a"
    assert queried[2].event_id == "evt-tie-b"
    assert queried[3].event_id == "evt-later"

    # Verify snapshot replacement (does not accumulate)
    ev5 = CalendarEvent(event_id="evt-solo", title="Solo Event", start_at=now + timedelta(hours=1), end_at=now + timedelta(hours=2), user_id=user_id)
    await calendar_manager.set_events([ev5], user_id=user_id)

    replaced, _ = await calendar_manager.get_events(
        start_at=now, end_at=now + timedelta(hours=10), user_id=user_id
    )
    assert len(replaced) == 1
    assert replaced[0].event_id == "evt-solo"


@pytest.mark.asyncio
async def test_calendar_manager_max_limits(calendar_manager, permission_manager):
    user_id = "user_limits"
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)

    now = datetime.now(timezone.utc)

    # 1. Exceeds MAX_SNAPSHOT_EVENTS (50)
    too_many = [
        CalendarEvent(event_id=f"evt-{i}", title=f"Event {i}", start_at=now, end_at=now + timedelta(minutes=30), user_id=user_id)
        for i in range(MAX_SNAPSHOT_EVENTS + 1)
    ]
    with pytest.raises(ValueError, match="exceeds maximum allowed of 50 events"):
        await calendar_manager.set_events(too_many, user_id=user_id)

    # 2. Query range exceeds MAX_QUERY_RANGE_DAYS (30)
    valid_event = CalendarEvent(event_id="evt-1", title="Event 1", start_at=now, end_at=now + timedelta(hours=1), user_id=user_id)
    await calendar_manager.set_events([valid_event], user_id=user_id)

    with pytest.raises(ValueError, match="exceeds maximum allowed of 30 days"):
        await calendar_manager.get_events(start_at=now, end_at=now + timedelta(days=35), user_id=user_id)
