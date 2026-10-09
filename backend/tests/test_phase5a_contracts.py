import pytest
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.services.identity.models import UserIdentity, DeviceIdentity, DEFAULT_LOCAL_USER_ID
from app.services.identity.manager import IdentityManager
from app.services.permissions.models import (
    PermissionType,
    PermissionStatus,
    PermissionRecord,
)
from app.services.permissions.manager import (
    PermissionManager,
    InMemoryPermissionStore,
)
from app.services.context.base import (
    ContextBlock,
    BaseContextProvider,
    ContextSensitivity,
)
from app.services.context.policy import ContextAccessPolicy
from app.services.context.engine import ContextEngine
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import ToolCall


# =====================================================================
# 1. Identity Contract Tests
# =====================================================================

def test_user_identity_defaults():
    user = UserIdentity()
    assert user.user_id == DEFAULT_LOCAL_USER_ID
    assert user.display_name == "Aurora User"
    assert isinstance(user.created_at, datetime)
    assert user.created_at.tzinfo is not None


def test_user_identity_custom():
    custom = UserIdentity(user_id="usr_alice", display_name="Alice Smith", metadata={"locale": "en_US"})
    assert custom.user_id == "usr_alice"
    assert custom.display_name == "Alice Smith"
    assert custom.metadata["locale"] == "en_US"


def test_device_identity_contract():
    device = DeviceIdentity(
        device_id="dev_iphone_16_pro",
        user_id="usr_alice",
        platform="ios"
    )
    assert device.device_id == "dev_iphone_16_pro"
    assert device.user_id == "usr_alice"
    assert device.platform == "ios"
    assert device.last_active_at is None


def test_identity_manager_get_and_set_current_user():
    mgr = IdentityManager()
    default_u = mgr.get_current_user()
    assert default_u.user_id == DEFAULT_LOCAL_USER_ID

    custom_u = UserIdentity(user_id="usr_bob", display_name="Bob")
    mgr.set_current_user(custom_u)
    assert mgr.get_current_user().user_id == "usr_bob"


def test_identity_manager_get_or_create_device():
    mgr = IdentityManager()
    dev1 = mgr.get_or_create_device("dev_uuid_12345", platform="ios")
    assert dev1.device_id == "dev_uuid_12345"
    assert dev1.platform == "ios"
    assert dev1.last_active_at is not None

    # Re-accessing updates last_active_at
    dev2 = mgr.get_or_create_device("dev_uuid_12345")
    assert dev2.device_id == "dev_uuid_12345"


# =====================================================================
# 2. Permission Contract & State Transition Tests
# =====================================================================

def test_permission_type_enum_values():
    assert PermissionType.LOCATION.value == "location"
    assert PermissionType.CALENDAR.value == "calendar"
    assert PermissionType.NOTIFICATIONS.value == "notifications"
    assert PermissionType.CONTACTS.value == "contacts"
    assert PermissionType.REMINDERS.value == "reminders"
    assert PermissionType.PHOTOS_FILES.value == "photos_files"


def test_permission_status_enum_values():
    assert PermissionStatus.NOT_DETERMINED.value == "not_determined"
    assert PermissionStatus.DENIED.value == "denied"
    assert PermissionStatus.AUTHORIZED.value == "authorized"
    assert PermissionStatus.RESTRICTED.value == "restricted"
    assert PermissionStatus.UNAVAILABLE.value == "unavailable"


@pytest.mark.parametrize("status,expected_authorized", [
    (PermissionStatus.AUTHORIZED, True),
    (PermissionStatus.NOT_DETERMINED, False),
    (PermissionStatus.DENIED, False),
    (PermissionStatus.RESTRICTED, False),
    (PermissionStatus.UNAVAILABLE, False),
])
def test_permission_record_authorization_rule(status, expected_authorized):
    """Rule: Only explicitly AUTHORIZED grants access. All other states block."""
    record = PermissionRecord(permission_type=PermissionType.LOCATION, status=status)
    assert record.is_authorized is expected_authorized


@pytest.mark.asyncio
async def test_permission_manager_state_transitions():
    store = InMemoryPermissionStore(initial_records={})
    manager = PermissionManager(store=store)

    # Initial state defaults to NOT_DETERMINED
    assert await manager.get_status(PermissionType.CALENDAR) == PermissionStatus.NOT_DETERMINED
    assert await manager.is_authorized(PermissionType.CALENDAR) is False

    # Update to AUTHORIZED
    await manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED)
    assert await manager.get_status(PermissionType.CALENDAR) == PermissionStatus.AUTHORIZED
    assert await manager.is_authorized(PermissionType.CALENDAR) is True

    # Transition to DENIED
    await manager.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED)
    assert await manager.get_status(PermissionType.CALENDAR) == PermissionStatus.DENIED
    assert await manager.is_authorized(PermissionType.CALENDAR) is False

    # Transition to RESTRICTED
    await manager.update_permission(PermissionType.CALENDAR, PermissionStatus.RESTRICTED)
    assert await manager.get_status(PermissionType.CALENDAR) == PermissionStatus.RESTRICTED
    assert await manager.is_authorized(PermissionType.CALENDAR) is False

    # Transition to UNAVAILABLE
    await manager.update_permission(PermissionType.CALENDAR, PermissionStatus.UNAVAILABLE)
    assert await manager.get_status(PermissionType.CALENDAR) == PermissionStatus.UNAVAILABLE
    assert await manager.is_authorized(PermissionType.CALENDAR) is False


# =====================================================================
# 3. Context Contracts, Sensitivity & Access Policy Tests
# =====================================================================

def test_context_sensitivity_values():
    assert ContextSensitivity.PUBLIC.value == "public"
    assert ContextSensitivity.PRIVATE.value == "private"
    assert ContextSensitivity.SENSITIVE.value == "sensitive"


def test_context_block_defaults_and_backward_compatibility():
    block = ContextBlock(title="Test", content="Some test content")
    assert block.sensitivity == ContextSensitivity.PUBLIC
    assert block.source == "system"
    assert block.freshness is None
    assert block.required_permission is None
    assert block.is_user_visible is True


class DummyProtectedProvider(BaseContextProvider):
    def __init__(self, perm: Optional[PermissionType] = None, sensitivity: ContextSensitivity = ContextSensitivity.SENSITIVE):
        self._perm = perm
        self._sensitivity = sensitivity

    @property
    def name(self) -> str:
        return "dummy_protected"

    @property
    def sensitivity(self) -> ContextSensitivity:
        return self._sensitivity

    @property
    def required_permission(self) -> Optional[PermissionType]:
        return self._perm

    async def get_context(self, session=None, query=None, **kwargs) -> Optional[ContextBlock]:
        return ContextBlock(
            title="Protected Data",
            content="Sensitive coordinates: 37.7749, -122.4194",
            sensitivity=self._sensitivity,
            required_permission=self._perm
        )


@pytest.mark.asyncio
async def test_context_policy_public_allowed_without_permission():
    policy = ContextAccessPolicy(permission_manager=PermissionManager(store=InMemoryPermissionStore(initial_records={})))
    public_block = ContextBlock(title="Public", content="Time is 12:00", sensitivity=ContextSensitivity.PUBLIC)

    allowed, reason = await policy.is_context_allowed(public_block)
    assert allowed is True
    assert reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_allowed", [
    (PermissionStatus.AUTHORIZED, True),
    (PermissionStatus.NOT_DETERMINED, False),
    (PermissionStatus.DENIED, False),
    (PermissionStatus.RESTRICTED, False),
    (PermissionStatus.UNAVAILABLE, False),
])
async def test_context_policy_permission_enforcement(status, expected_allowed):
    store = InMemoryPermissionStore(initial_records={})
    pm = PermissionManager(store=store)
    if status != PermissionStatus.NOT_DETERMINED:
        await pm.update_permission(PermissionType.LOCATION, status)

    policy = ContextAccessPolicy(permission_manager=pm)
    block = ContextBlock(
        title="Location",
        content="Current city: San Francisco",
        sensitivity=ContextSensitivity.SENSITIVE,
        required_permission=PermissionType.LOCATION
    )

    allowed, reason = await policy.is_context_allowed(block)
    assert allowed is expected_allowed
    if not expected_allowed:
        assert "Required permission 'location' is not authorized" in reason


@pytest.mark.asyncio
async def test_context_engine_integrates_policy():
    store = InMemoryPermissionStore(initial_records={})
    pm = PermissionManager(store=store)
    policy = ContextAccessPolicy(permission_manager=pm)

    # Provider requiring location permission
    provider = DummyProtectedProvider(perm=PermissionType.LOCATION, sensitivity=ContextSensitivity.SENSITIVE)
    engine = ContextEngine(providers=[provider], policy=policy)

    # 1. When LOCATION is NOT_DETERMINED -> blocked
    blocks = await engine.gather_context_blocks()
    assert len(blocks) == 0

    # 2. When LOCATION is DENIED -> blocked
    await pm.update_permission(PermissionType.LOCATION, PermissionStatus.DENIED)
    blocks = await engine.gather_context_blocks()
    assert len(blocks) == 0

    # 3. When LOCATION is AUTHORIZED -> included
    await pm.update_permission(PermissionType.LOCATION, PermissionStatus.AUTHORIZED)
    blocks = await engine.gather_context_blocks()
    assert len(blocks) == 1
    assert "Sensitive coordinates" in blocks[0].content


# =====================================================================
# 4. Tool/Action Authorization Tests
# =====================================================================

class ProtectedCalendarTool(BaseTool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="create_calendar_event",
            description="Simulated calendar event creator",
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"}
                },
                "required": ["title"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.CALENDAR]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "created", "title": params["title"]}


@pytest.mark.asyncio
async def test_tool_executor_blocks_unauthorized_tool():
    store = InMemoryPermissionStore(initial_records={})
    pm = PermissionManager(store=store)
    registry = ToolRegistry()
    registry.register(ProtectedCalendarTool())
    executor = ToolExecutor(registry=registry, permission_manager=pm)

    call = ToolCall(name="create_calendar_event", arguments={"title": "Team Sync"})

    # 1. When CALENDAR is not determined -> blocked immediately
    result = await executor.execute(call, is_pre_approved=True)
    assert result.success is False
    assert "Required permission 'calendar' is not authorized" in result.error

    # 2. When CALENDAR is DENIED -> blocked immediately
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED)
    result2 = await executor.execute(call, is_pre_approved=True)
    assert result2.success is False
    assert "Required permission 'calendar' is not authorized" in result2.error

    # 3. When CALENDAR is AUTHORIZED -> allowed to proceed
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED)
    result3 = await executor.execute(call, is_pre_approved=True)
    assert result3.success is True
    assert result3.output["status"] == "created"


@pytest.mark.asyncio
async def test_tool_executor_enforces_permission_before_confirmation():
    """
    If a tool requires permission AND confirmation:
    If permission is missing, it fails authorization rather than asking the user
    to confirm an impossible/blocked action.
    """
    store = InMemoryPermissionStore(initial_records={})
    pm = PermissionManager(store=store)
    registry = ToolRegistry()
    registry.register(ProtectedCalendarTool())
    executor = ToolExecutor(registry=registry, permission_manager=pm)

    call = ToolCall(name="create_calendar_event", arguments={"title": "Team Sync"})

    # Permission denied -> returns authorization failure directly without confirmation
    await pm.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED)
    result = await executor.execute(call, is_pre_approved=False)
    assert result.success is False
    assert "Action blocked: Required permission 'calendar'" in result.error
