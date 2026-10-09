import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from app.services.location.models import LocationSnapshot
from app.services.location.manager import LocationManager
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore


def test_location_model_validation_valid():
    now = datetime.now(timezone.utc)
    loc = LocationSnapshot(
        latitude=37.7749,
        longitude=-122.4194,
        accuracy_meters=10.5,
        captured_at=now,
        source="test",
        user_id="usr_test"
    )
    assert loc.latitude == 37.7749
    assert loc.longitude == -122.4194
    assert loc.accuracy_meters == 10.5
    assert loc.captured_at.tzinfo == timezone.utc
    assert loc.is_fresh() is True


def test_location_model_validation_invalid_bounds():
    now = datetime.now(timezone.utc)

    # Latitude > 90
    with pytest.raises(ValidationError):
        LocationSnapshot(latitude=91.0, longitude=0.0, accuracy_meters=5.0, captured_at=now)

    # Latitude < -90
    with pytest.raises(ValidationError):
        LocationSnapshot(latitude=-91.0, longitude=0.0, accuracy_meters=5.0, captured_at=now)

    # Longitude > 180
    with pytest.raises(ValidationError):
        LocationSnapshot(latitude=0.0, longitude=180.1, accuracy_meters=5.0, captured_at=now)

    # Longitude < -180
    with pytest.raises(ValidationError):
        LocationSnapshot(latitude=0.0, longitude=-180.1, accuracy_meters=5.0, captured_at=now)

    # Negative accuracy
    with pytest.raises(ValidationError):
        LocationSnapshot(latitude=0.0, longitude=0.0, accuracy_meters=-1.0, captured_at=now)


def test_location_timestamp_timezone_normalization():
    # Naive timestamp should be normalized to UTC
    naive = datetime(2026, 9, 22, 10, 0, 0)
    loc = LocationSnapshot(
        latitude=40.7128,
        longitude=-74.0060,
        accuracy_meters=8.0,
        captured_at=naive
    )
    assert loc.captured_at.tzinfo == timezone.utc


def test_location_freshness_window():
    now = datetime.now(timezone.utc)

    # Fresh snapshot (5 minutes old)
    fresh_loc = LocationSnapshot(
        latitude=51.5074,
        longitude=-0.1278,
        accuracy_meters=5.0,
        captured_at=now - timedelta(minutes=5)
    )
    assert fresh_loc.is_fresh(max_age_seconds=900) is True

    # Stale snapshot (20 minutes old, threshold is 15 minutes)
    stale_loc = LocationSnapshot(
        latitude=51.5074,
        longitude=-0.1278,
        accuracy_meters=5.0,
        captured_at=now - timedelta(minutes=20)
    )
    assert stale_loc.is_fresh(max_age_seconds=900) is False


@pytest.mark.asyncio
async def test_location_manager_permission_gating_and_isolation():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    loc_manager = LocationManager(permission_manager=perm_manager)

    user_a = "usr_alice"
    user_b = "usr_bob"

    now = datetime.now(timezone.utc)
    snap_a = LocationSnapshot(
        latitude=34.0522,
        longitude=-118.2437,
        accuracy_meters=12.0,
        captured_at=now,
        user_id=user_a
    )

    # 1. Set snapshot for User A
    loc_manager.set_current_location(snap_a, user_id=user_a)

    # 2. Blocked when permission NOT_DETERMINED
    res, reason = await loc_manager.get_current_location(user_id=user_a)
    assert res is None
    assert reason == "unauthorized"

    # 3. Blocked when permission DENIED
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.DENIED,
        user_id=user_a
    )
    res, reason = await loc_manager.get_current_location(user_id=user_a)
    assert res is None
    assert reason == "unauthorized"

    # 4. Blocked when permission RESTRICTED
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.RESTRICTED,
        user_id=user_a
    )
    res, reason = await loc_manager.get_current_location(user_id=user_a)
    assert res is None
    assert reason == "unauthorized"

    # 5. Succeeds when permission AUTHORIZED
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_a
    )
    res, reason = await loc_manager.get_current_location(user_id=user_a)
    assert res is not None
    assert reason == "available"
    assert res.latitude == 34.0522

    # 6. User B cannot see User A's location (User isolation)
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_b
    )
    res_b, reason_b = await loc_manager.get_current_location(user_id=user_b)
    assert res_b is None
    assert reason_b == "unavailable"

    # 7. Clear location
    loc_manager.clear_location(user_id=user_a)
    res_cleared, reason_cleared = await loc_manager.get_current_location(user_id=user_a)
    assert res_cleared is None
    assert reason_cleared == "unavailable"
