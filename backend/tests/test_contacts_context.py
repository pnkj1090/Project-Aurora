import pytest

from app.services.context.base import ContextSensitivity
from app.services.context.policy import ContextAccessPolicy
from app.services.context.engine import ContextEngine
from app.services.context.providers.contacts import ContactsContextProvider
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.contacts.models import ContactSnapshot
from app.services.contacts.manager import ContactManager


@pytest.fixture
def permission_manager():
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


@pytest.fixture
def contact_manager(permission_manager):
    return ContactManager(permission_manager=permission_manager)


@pytest.fixture
def access_policy(permission_manager):
    return ContextAccessPolicy(permission_manager=permission_manager)


def test_contacts_context_provider_metadata():
    provider = ContactsContextProvider()
    assert provider.name == "contacts"
    assert provider.context_type == "contacts"
    assert provider.source == "ios/contacts"
    assert provider.sensitivity == ContextSensitivity.SENSITIVE
    assert provider.required_permission == PermissionType.CONTACTS
    assert provider.priority == 35


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
async def test_contacts_context_policy_enforcement(
    contact_manager, permission_manager, access_policy, status, should_allow
):
    user_id = f"test_ctx_contacts_{status.value}"
    await permission_manager.update_permission(PermissionType.CONTACTS, status, user_id=user_id)

    provider = ContactsContextProvider(contact_manager=contact_manager)
    is_allowed, reason = await access_policy.is_context_allowed(provider, user_id=user_id)

    assert is_allowed is should_allow
    if not should_allow:
        assert "not authorized" in reason


@pytest.mark.asyncio
async def test_contacts_context_leakage_regression_test(contact_manager, permission_manager, access_policy):
    """
    CRITICAL AMENDMENT 6 REGRESSION TEST:
    Given:
      - Contacts permission = AUTHORIZED
      - ephemeral contact store contains contacts
    When:
      - a generic chat/context request contains NO contacts relevance/query
    Then:
      - ContactsContextProvider contributes NO contact information (returns None)
      - The ContextEngine system prompt contains NO contact data.
    """
    user_id = "user_leakage_test"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    # Populate contacts
    c1 = ContactSnapshot(
        contact_id="cnt-secret-1",
        display_name="Sensitive Colleague",
        phone_numbers=["+15559998888"],
        email_addresses=["sensitive@corp.com"],
        organization="Confidential Partners"
    )
    c2 = ContactSnapshot(
        contact_id="cnt-secret-2",
        display_name="Private Doctor",
        phone_numbers=["+15557776666"],
        organization="City Clinic"
    )
    await contact_manager.set_contacts([c1, c2], user_id=user_id)

    provider = ContactsContextProvider(contact_manager=contact_manager)

    # 1. Calling provider directly with no query -> returns None
    block_generic = await provider.get_context(user_id=user_id)
    assert block_generic is None

    # 2. In ContextEngine: building generic prompt with no contact query -> zero contact leakage
    engine = ContextEngine(providers=[provider], policy=access_policy)
    prompt = await engine.build_system_prompt(user_id=user_id)

    assert "Sensitive Colleague" not in prompt
    assert "Private Doctor" not in prompt
    assert "Confidential Partners" not in prompt
    assert "5559998888" not in prompt
    assert "Relevant Contacts" not in prompt


@pytest.mark.asyncio
async def test_contacts_context_opt_in_with_explicit_query(contact_manager, permission_manager):
    """
    AMENDMENT 2 & 5:
    When an explicit relevance query is provided:
      - Returns bounded ContextBlock
      - Exposes display name and organization ONLY
      - Sensitive phone numbers and emails are NOT injected into broad context
    """
    user_id = "user_opt_in_test"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    c = ContactSnapshot(
        contact_id="cnt-target",
        display_name="Target Person",
        phone_numbers=["+1234567890"],
        email_addresses=["target@person.org"],
        organization="Target Global"
    )
    await contact_manager.set_contacts([c], user_id=user_id)

    provider = ContactsContextProvider(contact_manager=contact_manager)

    # Explicit query provided
    block = await provider.get_context(query="Target", user_id=user_id)
    assert block is not None
    assert block.sensitivity == ContextSensitivity.SENSITIVE
    assert block.required_permission == PermissionType.CONTACTS
    assert "Target Person" in block.content
    assert "Target Global" in block.content

    # Phone number and email are minimized out of context block
    assert "+1234567890" not in block.content
    assert "target@person.org" not in block.content
