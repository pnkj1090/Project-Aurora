import pytest
from datetime import datetime, timezone, timedelta

from app.services.context.base import ContextSensitivity
from app.services.context.policy import ContextAccessPolicy
from app.services.context.providers.location import LocationContextProvider
from app.services.context.engine import ContextEngine
from app.services.location.models import LocationSnapshot
from app.services.location.manager import LocationManager
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore


@pytest.mark.asyncio
async def test_location_context_provider_metadata():
    provider = LocationContextProvider()
    assert provider.name == "location"
    assert provider.context_type == "location"
    assert provider.sensitivity == ContextSensitivity.SENSITIVE
    assert provider.required_permission == PermissionType.LOCATION


@pytest.mark.asyncio
async def test_location_context_policy_gating():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    loc_manager = LocationManager(permission_manager=perm_manager)
    policy = ContextAccessPolicy(permission_manager=perm_manager)

    provider = LocationContextProvider(location_manager=loc_manager)
    user_id = "usr_ctx_test"

    # Set fresh snapshot
    now = datetime.now(timezone.utc)
    snap = LocationSnapshot(
        latitude=48.8566,
        longitude=2.3522,
        accuracy_meters=15.0,
        captured_at=now,
        user_id=user_id
    )
    loc_manager.set_current_location(snap, user_id=user_id)

    # 1. NOT_DETERMINED: Policy blocks provider
    allowed, reason = await policy.is_context_allowed(provider, user_id=user_id)
    assert allowed is False
    assert "not authorized" in reason

    block = await provider.get_context(user_id=user_id)
    assert block is None

    # 2. DENIED: Policy blocks provider
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.DENIED,
        user_id=user_id
    )
    allowed, reason = await policy.is_context_allowed(provider, user_id=user_id)
    assert allowed is False
    assert "denied" in reason

    # 3. AUTHORIZED: Policy allows provider & block is generated
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_id
    )
    allowed, reason = await policy.is_context_allowed(provider, user_id=user_id)
    assert allowed is True
    assert reason is None

    block = await provider.get_context(user_id=user_id)
    assert block is not None
    assert block.title == "Current Location"
    assert block.sensitivity == ContextSensitivity.SENSITIVE
    assert "48.8566" in block.content


@pytest.mark.asyncio
async def test_location_context_omitted_when_stale():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    loc_manager = LocationManager(permission_manager=perm_manager)

    provider = LocationContextProvider(location_manager=loc_manager, max_age_seconds=900)
    user_id = "usr_stale_test"

    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_id
    )

    # Stale snapshot: captured 30 minutes ago
    old_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    snap = LocationSnapshot(
        latitude=35.6762,
        longitude=139.6503,
        accuracy_meters=20.0,
        captured_at=old_time,
        user_id=user_id
    )
    loc_manager.set_current_location(snap, user_id=user_id)

    # Provider should return None because snapshot has expired
    block = await provider.get_context(user_id=user_id)
    assert block is None


@pytest.mark.asyncio
async def test_context_engine_includes_location_when_authorized():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    loc_manager = LocationManager(permission_manager=perm_manager)
    policy = ContextAccessPolicy(permission_manager=perm_manager)

    user_id = "usr_engine_test"
    provider = LocationContextProvider(location_manager=loc_manager)

    engine = ContextEngine(policy=policy)
    engine.register_provider(provider)

    # Set fresh snapshot
    now = datetime.now(timezone.utc)
    snap = LocationSnapshot(
        latitude=37.7749,
        longitude=-122.4194,
        accuracy_meters=10.0,
        captured_at=now,
        user_id=user_id
    )
    loc_manager.set_current_location(snap, user_id=user_id)

    # 1. Unauthorized: prompt does NOT contain location block
    prompt_unauth = await engine.build_system_prompt(user_id=user_id)
    assert "Current Location" not in prompt_unauth

    # 2. Authorized: prompt contains location block
    await perm_manager.update_permission(
        permission_type=PermissionType.LOCATION,
        status=PermissionStatus.AUTHORIZED,
        user_id=user_id
    )
    prompt_auth = await engine.build_system_prompt(user_id=user_id)
    assert "Current Location" in prompt_auth
    assert "37.7749" in prompt_auth
