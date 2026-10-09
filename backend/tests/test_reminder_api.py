import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.reminders.store import InMemoryReminderStore
from app.services.reminders.manager import ReminderManager, get_reminder_manager_dependency
from app.services.permissions.models import PermissionType, PermissionStatus, PermissionRecord
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore, get_permission_manager_dependency
from app.services.context.engine import ContextEngine


@pytest.fixture
def override_reminder_manager():
    store = InMemoryReminderStore()
    manager = ReminderManager(store=store)
    perm_store = InMemoryPermissionStore()
    for uid in ("usr_api_test", "usr_other_user"):
        for p in (PermissionType.REMINDERS, "reminders", "reminders:write"):
            perm_store._store[(uid, p)] = PermissionRecord(
                permission_type=p,
                status=PermissionStatus.AUTHORIZED,
                user_id=uid,
                source="test"
            )
    perm_manager = PermissionManager(store=perm_store)
    app.dependency_overrides[get_reminder_manager_dependency] = lambda: manager
    app.dependency_overrides[get_permission_manager_dependency] = lambda: perm_manager
    yield manager
    app.dependency_overrides.pop(get_reminder_manager_dependency, None)
    app.dependency_overrides.pop(get_permission_manager_dependency, None)


@pytest.mark.asyncio
async def test_reminder_rest_api_lifecycle(override_reminder_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_headers = {"X-User-ID": "usr_api_test"}
        sched_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        # 1. Create reminder
        create_resp = await client.post(
            "/api/v1/reminders",
            headers=user_headers,
            json={
                "title": "Dentist Appointment",
                "scheduled_at": sched_time,
                "body": "Bring dental insurance card",
            }
        )
        assert create_resp.status_code == 201
        data = create_resp.json()
        assert data["title"] == "Dentist Appointment"
        assert data["status"] == "scheduled"
        reminder_id = data["id"]

        # 2. Get reminder
        get_resp = await client.get(f"/api/v1/reminders/{reminder_id}", headers=user_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == reminder_id

        # 3. Cross-user isolation: User B cannot access User A's reminder
        b_resp = await client.get(
            f"/api/v1/reminders/{reminder_id}",
            headers={"X-User-ID": "usr_other_user"}
        )
        assert b_resp.status_code == 404

        # 4. List reminders
        list_resp = await client.get("/api/v1/reminders", headers=user_headers)
        assert list_resp.status_code == 200
        list_data = list_resp.json()
        assert list_data["total"] == 1
        assert list_data["items"][0]["id"] == reminder_id

        # 5. Patch reminder (allowed fields: title, body, scheduled_at)
        new_sched = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        patch_resp = await client.patch(
            f"/api/v1/reminders/{reminder_id}",
            headers=user_headers,
            json={
                "title": "Updated Dentist Visit",
                "scheduled_at": new_sched,
            }
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["title"] == "Updated Dentist Visit"

        # 6. Cancel reminder
        cancel_resp = await client.post(
            f"/api/v1/reminders/{reminder_id}/cancel",
            headers=user_headers
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        # 7. Safe soft delete
        del_resp = await client.delete(
            f"/api/v1/reminders/{reminder_id}",
            headers=user_headers
        )
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True

        # Now get should return 404
        get_after_del = await client.get(f"/api/v1/reminders/{reminder_id}", headers=user_headers)
        assert get_after_del.status_code == 404


@pytest.mark.asyncio
async def test_reminders_not_injected_into_context_engine():
    """
    Phase 5C / Phase 6A specification:
    'Context tests should ensure reminders are NOT automatically injected into normal conversation context'
    """
    engine = ContextEngine()

    # Check system prompt generation
    prompt = await engine.build_system_prompt()
    assert "reminder" not in prompt.lower()
