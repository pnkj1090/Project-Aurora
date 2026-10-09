import pytest
from datetime import datetime, timezone

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.contacts.models import ContactSnapshot
from app.services.contacts.manager import (
    ContactManager,
    MAX_SNAPSHOT_CONTACTS,
    MAX_QUERY_RESULTS,
    MAX_QUERY_LENGTH,
)


@pytest.fixture
def permission_manager():
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


@pytest.fixture
def contact_manager(permission_manager):
    return ContactManager(permission_manager=permission_manager)


def test_contact_model_validation():
    now = datetime.now(timezone.utc)
    c = ContactSnapshot(
        contact_id="cnt-1",
        display_name="Alice Smith",
        phone_numbers=["+15551234567", "555-9876"],
        email_addresses=["Alice@Example.com"],
        organization="Acme Corp",
        source="ios_contacts",
    )
    assert c.contact_id == "cnt-1"
    assert c.display_name == "Alice Smith"
    assert c.phone_numbers == ["+15551234567", "555-9876"]
    assert c.email_addresses == ["alice@example.com"]  # Lowercased
    assert c.organization == "Acme Corp"
    assert c.user_id == DEFAULT_LOCAL_USER_ID
    assert c.captured_at.tzinfo is not None


def test_contact_model_rejects_empty_identity():
    with pytest.raises(ValueError, match="display_name cannot be empty"):
        ContactSnapshot(contact_id="cnt-1", display_name="   ")

    with pytest.raises(ValueError, match="contact_id cannot be empty"):
        ContactSnapshot(contact_id="   ", display_name="Bob Jones")


def test_contact_model_rejects_naive_datetime():
    with pytest.raises(ValueError, match="captured_at must be a timezone-aware datetime"):
        ContactSnapshot(
            contact_id="cnt-naive",
            display_name="Charlie Brown",
            captured_at=datetime(2026, 9, 22, 12, 0, 0)
        )


@pytest.mark.asyncio
async def test_contact_manager_permission_gating(contact_manager, permission_manager):
    user_id = "user_perm_gating"

    # Default is NOT_DETERMINED -> unauthorized
    auth = await contact_manager.is_contacts_authorized(user_id=user_id)
    assert auth is False

    contacts, reason = await contact_manager.get_contacts(user_id=user_id)
    assert contacts == []
    assert reason == "unauthorized"

    # Grant AUTHORIZED
    await permission_manager.update_permission(
        PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id
    )
    auth_after = await contact_manager.is_contacts_authorized(user_id=user_id)
    assert auth_after is True


@pytest.mark.asyncio
async def test_contact_manager_user_isolation(contact_manager, permission_manager):
    user_a = "user_alice"
    user_b = "user_bob"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_a)
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_b)

    c_a = ContactSnapshot(contact_id="cnt-a", display_name="Alice Contact", user_id=user_a)
    c_b = ContactSnapshot(contact_id="cnt-b", display_name="Bob Contact", user_id=user_b)

    await contact_manager.set_contacts([c_a], user_id=user_a)
    await contact_manager.set_contacts([c_b], user_id=user_b)

    res_a, _ = await contact_manager.get_contacts(user_id=user_a)
    res_b, _ = await contact_manager.get_contacts(user_id=user_b)

    assert len(res_a) == 1
    assert res_a[0].contact_id == "cnt-a"
    assert len(res_b) == 1
    assert res_b[0].contact_id == "cnt-b"


@pytest.mark.asyncio
async def test_contact_manager_duplicate_contact_id_rejection(contact_manager, permission_manager):
    user_id = "user_dup_test"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    dup1 = ContactSnapshot(contact_id="dup-1", display_name="John Doe")
    dup2 = ContactSnapshot(contact_id="dup-1", display_name="John Another")

    with pytest.raises(ValueError, match="Duplicate contact ID 'dup-1' detected"):
        await contact_manager.set_contacts([dup1, dup2], user_id=user_id)


@pytest.mark.asyncio
async def test_contact_manager_deterministic_ordering_and_replacement(contact_manager, permission_manager):
    user_id = "user_order_test"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    c1 = ContactSnapshot(contact_id="cnt-z", display_name="Zoe Adams")
    c2 = ContactSnapshot(contact_id="cnt-a", display_name="Aaron Paul")
    c3 = ContactSnapshot(contact_id="cnt-b", display_name="Aaron Paul")  # Same name, tie-breaker on id

    await contact_manager.set_contacts([c1, c2, c3], user_id=user_id)
    results, _ = await contact_manager.get_contacts(user_id=user_id)

    assert len(results) == 3
    assert results[0].contact_id == "cnt-a"
    assert results[1].contact_id == "cnt-b"
    assert results[2].contact_id == "cnt-z"

    # Ingesting new snapshot replaces previous snapshot
    new_c = ContactSnapshot(contact_id="cnt-single", display_name="Solo Contact")
    await contact_manager.set_contacts([new_c], user_id=user_id)
    results_after, _ = await contact_manager.get_contacts(user_id=user_id)
    assert len(results_after) == 1
    assert results_after[0].contact_id == "cnt-single"


@pytest.mark.asyncio
async def test_contact_manager_query_scoping_and_normalization(contact_manager, permission_manager):
    user_id = "user_query_scope"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    # Given contacts: John, Sarah, Mike (Amendment 7)
    john = ContactSnapshot(contact_id="cnt-1", display_name="John Doe", organization="Acme")
    sarah = ContactSnapshot(contact_id="cnt-2", display_name="Sarah Connor", organization="Cyberdyne")
    mike = ContactSnapshot(contact_id="cnt-3", display_name="Mike Ross", organization="Pearson Hardman")

    await contact_manager.set_contacts([john, sarah, mike], user_id=user_id)

    # When searching "  jOHn  " (case-insensitive, trimmed)
    res_john, _ = await contact_manager.get_contacts(query="  jOHn  ", user_id=user_id)
    assert len(res_john) == 1
    assert res_john[0].display_name == "John Doe"

    # Unrelated contacts (Sarah, Mike) are NOT returned
    ids = {c.contact_id for c in res_john}
    assert "cnt-2" not in ids
    assert "cnt-3" not in ids


@pytest.mark.asyncio
async def test_contact_manager_search_by_phone_email_org(contact_manager, permission_manager):
    user_id = "user_multi_field"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    c = ContactSnapshot(
        contact_id="cnt-10",
        display_name="Jane Doe",
        phone_numbers=["+19876543210"],
        email_addresses=["jane.doe@hooli.xyz"],
        organization="Hooli"
    )
    await contact_manager.set_contacts([c], user_id=user_id)

    # Search by organization
    res_org, _ = await contact_manager.get_contacts(query="hooli", user_id=user_id)
    assert len(res_org) == 1

    # Search by phone number
    res_phone, _ = await contact_manager.get_contacts(query="987654", user_id=user_id)
    assert len(res_phone) == 1

    # Search by email
    res_email, _ = await contact_manager.get_contacts(query="hooli.xyz", user_id=user_id)
    assert len(res_email) == 1


@pytest.mark.asyncio
async def test_contact_manager_limits(contact_manager, permission_manager):
    user_id = "user_limits"
    await permission_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)

    # Snapshot size limit: MAX_SNAPSHOT_CONTACTS (100)
    oversized = [
        ContactSnapshot(contact_id=f"c-{i}", display_name=f"Person {i}")
        for i in range(MAX_SNAPSHOT_CONTACTS + 1)
    ]
    with pytest.raises(ValueError, match="Snapshot exceeds maximum allowed contacts limit"):
        await contact_manager.set_contacts(oversized, user_id=user_id)

    # Query length limit: MAX_QUERY_LENGTH (100)
    long_query = "a" * (MAX_QUERY_LENGTH + 1)
    with pytest.raises(ValueError, match="Query length exceeds maximum limit"):
        await contact_manager.get_contacts(query=long_query, user_id=user_id)

    # Result count limit: capped at MAX_QUERY_RESULTS (50)
    contacts = [
        ContactSnapshot(contact_id=f"c-{i}", display_name=f"User {i:02d}")
        for i in range(60)
    ]
    await contact_manager.set_contacts(contacts, user_id=user_id)
    res, _ = await contact_manager.get_contacts(max_results=100, user_id=user_id)
    assert len(res) == MAX_QUERY_RESULTS
