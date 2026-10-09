import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import default_permission_manager
from app.services.calendar import get_calendar_manager


from app.services.calendar.manager import CalendarManager, get_calendar_manager_dependency
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore


@pytest.fixture
def override_calendar_manager():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    cal_manager = CalendarManager(permission_manager=perm_manager)
    app.dependency_overrides[get_calendar_manager_dependency] = lambda: cal_manager
    yield cal_manager, perm_manager
    app.dependency_overrides.pop(get_calendar_manager_dependency, None)


@pytest.mark.asyncio
async def test_calendar_api_unauthorized_get(override_calendar_manager):
    cal_manager, perm_manager = override_calendar_manager
    user_id = "user_api_unauth"
    await perm_manager.update_permission(
        PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=user_id
    )

    now = datetime.now(timezone.utc)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(
            "/api/v1/calendar/events",
            params={
                "start_at": now.isoformat(),
                "end_at": (now + timedelta(hours=4)).isoformat(),
            },
            headers={"X-User-ID": user_id},
        )
        assert res.status_code == 403
        assert "not authorized" in res.json()["detail"]


@pytest.mark.asyncio
async def test_calendar_api_ingest_and_query_flow(override_calendar_manager):
    cal_manager, perm_manager = override_calendar_manager
    user_id = "user_api_1"
    await perm_manager.update_permission(
        PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id
    )

    now = datetime.now(timezone.utc)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Ingest ephemeral snapshot
        payload = {
            "events": [
                {
                    "event_id": "evt-api-1",
                    "title": "Strategy Session",
                    "start_at": (now + timedelta(hours=1)).isoformat(),
                    "end_at": (now + timedelta(hours=2)).isoformat(),
                    "all_day": False,
                    "calendar_name": "Executive",
                    "location": "Boardroom",
                    "notes_available": False,
                }
            ],
            "user_id": user_id,
        }
        ingest_res = await client.post(
            "/api/v1/calendar/events",
            json=payload,
            headers={"X-User-ID": user_id},
        )
        assert ingest_res.status_code == 201
        data = ingest_res.json()
        assert data["status"] == "success"
        assert data["count"] == 1
        assert data["events"][0]["event_id"] == "evt-api-1"

        # 2. Query events
        query_res = await client.get(
            "/api/v1/calendar/events",
            params={
                "start_at": now.isoformat(),
                "end_at": (now + timedelta(hours=5)).isoformat(),
                "max_results": 10,
            },
            headers={"X-User-ID": user_id},
        )
        assert query_res.status_code == 200
        qdata = query_res.json()
        assert qdata["count"] == 1
        assert qdata["events"][0]["title"] == "Strategy Session"


@pytest.mark.asyncio
async def test_calendar_api_cross_user_injection_rejected(override_calendar_manager):
    cal_manager, perm_manager = override_calendar_manager
    user_alice = "user_alice"
    user_bob = "user_bob"
    await perm_manager.update_permission(
        PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_alice
    )
    await perm_manager.update_permission(
        PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_bob
    )

    now = datetime.now(timezone.utc)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Alice attempting to submit calendar events with Bob's user_id in payload body
        payload = {
            "events": [
                {
                    "event_id": "evt-injected",
                    "title": "Secret Meeting",
                    "start_at": (now + timedelta(hours=1)).isoformat(),
                    "end_at": (now + timedelta(hours=2)).isoformat(),
                }
            ],
            "user_id": user_bob,  # Mismatched!
        }
        res = await client.post(
            "/api/v1/calendar/events",
            json=payload,
            headers={"X-User-ID": user_alice},
        )
        assert res.status_code == 403
        assert "Cross-user calendar injection rejected" in res.json()["detail"]


@pytest.mark.asyncio
async def test_calendar_api_invalid_query_range(override_calendar_manager):
    cal_manager, perm_manager = override_calendar_manager
    user_id = "user_api_range"
    await perm_manager.update_permission(
        PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id
    )

    now = datetime.now(timezone.utc)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Range exceeding 30 days
        res = await client.get(
            "/api/v1/calendar/events",
            params={
                "start_at": now.isoformat(),
                "end_at": (now + timedelta(days=40)).isoformat(),
            },
            headers={"X-User-ID": user_id},
        )
        assert res.status_code == 400
        assert "exceeds maximum" in res.json()["detail"]
