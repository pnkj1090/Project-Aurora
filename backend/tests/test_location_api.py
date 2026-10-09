import pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.location.models import LocationSnapshot
from app.services.location.manager import LocationManager, get_location_manager_dependency
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore


@pytest.fixture
def override_location_manager():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    loc_manager = LocationManager(permission_manager=perm_manager)
    app.dependency_overrides[get_location_manager_dependency] = lambda: loc_manager
    yield loc_manager, perm_manager
    app.dependency_overrides.pop(get_location_manager_dependency, None)


@pytest.mark.asyncio
async def test_location_api_endpoints(override_location_manager):
    loc_manager, perm_manager = override_location_manager
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_headers = {"X-User-ID": "usr_loc_api"}

        # 1. GET /api/v1/location/current before permission -> 403 Forbidden
        resp_unauth = await client.get("/api/v1/location/current", headers=user_headers)
        assert resp_unauth.status_code == 403

        # 2. Grant permission
        await perm_manager.update_permission(
            permission_type=PermissionType.LOCATION,
            status=PermissionStatus.AUTHORIZED,
            user_id="usr_loc_api"
        )

        # 3. GET /api/v1/location/current before snapshot reported -> 404 Not Found
        resp_none = await client.get("/api/v1/location/current", headers=user_headers)
        assert resp_none.status_code == 404

        # 4. POST /api/v1/location/current to report location snapshot
        resp_post = await client.post(
            "/api/v1/location/current",
            headers=user_headers,
            json={
                "latitude": 37.7749,
                "longitude": -122.4194,
                "accuracy_meters": 10.0,
                "source": "ios_core_location"
            }
        )
        assert resp_post.status_code == 201
        data = resp_post.json()
        assert data["latitude"] == 37.7749
        assert data["longitude"] == -122.4194
        assert data["user_id"] == "usr_loc_api"

        # 5. GET /api/v1/location/current after reporting -> 200 OK
        resp_get = await client.get("/api/v1/location/current", headers=user_headers)
        assert resp_get.status_code == 200
        get_data = resp_get.json()
        assert get_data["latitude"] == 37.7749
        assert get_data["accuracy_meters"] == 10.0

        # 6. User B cannot access User A's location
        other_headers = {"X-User-ID": "usr_other"}
        await perm_manager.update_permission(
            permission_type=PermissionType.LOCATION,
            status=PermissionStatus.AUTHORIZED,
            user_id="usr_other"
        )
        resp_other = await client.get("/api/v1/location/current", headers=other_headers)
        assert resp_other.status_code == 404
