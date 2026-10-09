import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.contacts.manager import ContactManager, get_contact_manager_dependency


@pytest.fixture
def override_contact_manager():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)
    cont_manager = ContactManager(permission_manager=perm_manager)
    app.dependency_overrides[get_contact_manager_dependency] = lambda: cont_manager
    yield cont_manager, perm_manager
    app.dependency_overrides.pop(get_contact_manager_dependency, None)


@pytest.mark.asyncio
async def test_contacts_api_unauthorized_access(override_contact_manager):
    cont_manager, perm_manager = override_contact_manager
    user_id = "usr_api_unauth"
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET before permission -> 403
        res_get = await client.get("/api/v1/contacts", headers={"X-User-ID": user_id})
        assert res_get.status_code == 403
        assert "not authorized" in res_get.json()["detail"]

        # 2. POST before permission -> 403
        res_post = await client.post(
            "/api/v1/contacts/snapshot",
            headers={"X-User-ID": user_id},
            json={"contacts": [{"contact_id": "c1", "display_name": "Test"}]}
        )
        assert res_post.status_code == 403


@pytest.mark.asyncio
async def test_contacts_api_ingest_and_query_flow(override_contact_manager):
    cont_manager, perm_manager = override_contact_manager
    user_id = "usr_api_flow"
    await perm_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Ingest bounded ephemeral snapshot
        payload = {
            "contacts": [
                {
                    "contact_id": "cnt-api-1",
                    "display_name": "Gordon Freeman",
                    "phone_numbers": ["+15554321"],
                    "email_addresses": ["gordon@blackmesa.gov"],
                    "organization": "Black Mesa",
                },
                {
                    "contact_id": "cnt-api-2",
                    "display_name": "Alyx Vance",
                    "phone_numbers": ["+15558765"],
                    "organization": "Resistance",
                }
            ],
            "user_id": user_id,
        }
        res_ingest = await client.post(
            "/api/v1/contacts/snapshot",
            headers={"X-User-ID": user_id},
            json=payload
        )
        assert res_ingest.status_code == 201
        data = res_ingest.json()
        assert data["status"] == "success"
        assert data["count"] == 2

        # 2. Search for "Gordon"
        res_query = await client.get(
            "/api/v1/contacts",
            params={"query": "Gordon"},
            headers={"X-User-ID": user_id}
        )
        assert res_query.status_code == 200
        qdata = res_query.json()
        assert qdata["count"] == 1
        assert qdata["contacts"][0]["display_name"] == "Gordon Freeman"
        assert qdata["contacts"][0]["organization"] == "Black Mesa"
        assert qdata["contacts"][0]["phone_numbers"] == ["+15554321"]

        # 3. Search with no matches
        res_empty = await client.get(
            "/api/v1/contacts",
            params={"query": "G-Man"},
            headers={"X-User-ID": user_id}
        )
        assert res_empty.status_code == 200
        assert res_empty.json()["count"] == 0
        assert res_empty.json()["contacts"] == []


@pytest.mark.asyncio
async def test_contacts_api_cross_user_injection_rejection(override_contact_manager):
    cont_manager, perm_manager = override_contact_manager
    user_alice = "user_alice"
    user_bob = "user_bob"
    await perm_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_alice)
    await perm_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_bob)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Alice tries to submit a snapshot with Bob's user_id in the body
        payload_body_mismatch = {
            "contacts": [{"contact_id": "c1", "display_name": "Hacked Contact"}],
            "user_id": user_bob,
        }
        res1 = await client.post(
            "/api/v1/contacts/snapshot",
            headers={"X-User-ID": user_alice},
            json=payload_body_mismatch
        )
        assert res1.status_code == 403
        assert "Cross-user contact injection rejected" in res1.json()["detail"]

        # Alice tries to submit an item with Bob's user_id
        payload_item_mismatch = {
            "contacts": [{"contact_id": "c2", "display_name": "Hacked Item", "user_id": user_bob}],
            "user_id": user_alice,
        }
        res2 = await client.post(
            "/api/v1/contacts/snapshot",
            headers={"X-User-ID": user_alice},
            json=payload_item_mismatch
        )
        assert res2.status_code == 403
        assert "Cross-user contact injection rejected" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_contacts_api_user_isolation(override_contact_manager):
    cont_manager, perm_manager = override_contact_manager
    user_a = "user_iso_a"
    user_b = "user_iso_b"
    await perm_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_a)
    await perm_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_b)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Ingest contact for User A
        await client.post(
            "/api/v1/contacts/snapshot",
            headers={"X-User-ID": user_a},
            json={"contacts": [{"contact_id": "cnt-a", "display_name": "Alice Private"}]}
        )

        # User B queries contacts -> should see 0 contacts
        res_b = await client.get("/api/v1/contacts", headers={"X-User-ID": user_b})
        assert res_b.status_code == 200
        assert res_b.json()["count"] == 0
        assert res_b.json()["contacts"] == []


@pytest.mark.asyncio
async def test_contacts_api_query_validation(override_contact_manager):
    cont_manager, perm_manager = override_contact_manager
    user_id = "user_api_validation"
    await perm_manager.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id=user_id)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Query length > 100
        res = await client.get(
            "/api/v1/contacts",
            params={"query": "x" * 105},
            headers={"X-User-ID": user_id}
        )
        # FastAPI query validation returns 422 Unprocessable Entity
        assert res.status_code == 422
