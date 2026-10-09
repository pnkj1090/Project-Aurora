import pytest
import pytest_asyncio
import hashlib
from datetime import datetime, timezone
import aiosqlite
import secrets
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.tasks import (
    InMemoryTaskStore,
    TaskManager,
    get_task_manager_dependency,
)
from app.services.commitments import (
    InMemoryCommitmentStore,
    CommitmentManager,
    get_commitment_manager_dependency,
)
from app.services.identity.auth import IdentityResolver, SQLiteCredentialStore, get_credential_store_dependency, get_identity_resolver_dependency
from app.services.permissions import InMemoryPermissionStore, PermissionManager, PermissionStatus, PermissionType, get_permission_manager_dependency
from app.services.identity.models import DEFAULT_LOCAL_USER_ID


@pytest_asyncio.fixture
async def clean_managers(tmp_path):
    task_store = InMemoryTaskStore()
    task_manager = TaskManager(store=task_store)

    cmt_store = InMemoryCommitmentStore()
    cmt_manager = CommitmentManager(store=cmt_store)

    credential_store = SQLiteCredentialStore(str(tmp_path / "test-auth.db"))
    secret = await credential_store.issue_provisioning()
    bearer = await credential_store.redeem_provisioning(hashlib.sha256(secret.encode("ascii")).hexdigest())
    permissions = PermissionManager(InMemoryPermissionStore())
    for permission in (PermissionType.TASKS, PermissionType.COMMITMENTS):
        await permissions.update_permission(permission, PermissionStatus.AUTHORIZED, user_id=DEFAULT_LOCAL_USER_ID)

    app.dependency_overrides[get_task_manager_dependency] = lambda: task_manager
    app.dependency_overrides[get_commitment_manager_dependency] = lambda: cmt_manager
    app.dependency_overrides[get_credential_store_dependency] = lambda: credential_store
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: IdentityResolver(credential_store)
    app.dependency_overrides[get_permission_manager_dependency] = lambda: permissions

    yield {"tasks": task_manager, "commitments": cmt_manager, "headers": {"Authorization": f"Bearer {bearer.credential}"}, "store": credential_store, "permissions": permissions}

    app.dependency_overrides.pop(get_task_manager_dependency, None)
    app.dependency_overrides.pop(get_commitment_manager_dependency, None)
    app.dependency_overrides.pop(get_credential_store_dependency, None)
    app.dependency_overrides.pop(get_identity_resolver_dependency, None)
    app.dependency_overrides.pop(get_permission_manager_dependency, None)


@pytest.mark.asyncio
async def test_task_rest_api_lifecycle(clean_managers):
    headers = clean_managers["headers"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create task
        resp = await client.post("/api/v1/tasks", json={
            "title": "Build iOS interface",
            "description": "SwiftUI views for tasks",
            "priority": "high",
            "tags": ["ios", "ui"]
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Build iOS interface"
        assert data["status"] == "pending"
        task_id = data["id"]

        # 2. Get task
        get_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == task_id

        # 3. Update task
        patch_resp = await client.patch(f"/api/v1/tasks/{task_id}", json={
            "title": "Build iOS and macOS interface",
            "priority": "medium"
        }, headers=headers)
        assert patch_resp.status_code == 200
        assert patch_resp.json()["title"] == "Build iOS and macOS interface"
        assert patch_resp.json()["priority"] == "medium"

        # 4. Complete task
        comp_resp = await client.post(f"/api/v1/tasks/{task_id}/complete", headers=headers)
        assert comp_resp.status_code == 200
        assert comp_resp.json()["status"] == "completed"
        assert comp_resp.json()["completed_at"] is not None
        assert comp_resp.json()["is_active"] is True  # Distinct from deletion

        # 5. Cannot cancel completed task
        cancel_resp = await client.post(f"/api/v1/tasks/{task_id}/cancel", headers=headers)
        assert cancel_resp.status_code == 400

        # 6. Delete task (soft delete)
        del_resp = await client.delete(f"/api/v1/tasks/{task_id}", headers=headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True

        # Now 404 in normal get
        get_deleted = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
        assert get_deleted.status_code == 404


@pytest.mark.asyncio
async def test_commitment_rest_api_lifecycle(clean_managers):
    headers = clean_managers["headers"]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create commitment
        resp = await client.post("/api/v1/commitments", json={
            "title": "Submit project review",
            "description": "Prepare summary document",
            "priority": "high",
            "tags": ["review", "aurora"]
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Submit project review"
        assert data["status"] == "active"
        cmt_id = data["id"]

        # 2. Get commitment
        get_resp = await client.get(f"/api/v1/commitments/{cmt_id}", headers=headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == cmt_id

        # 3. Update commitment
        patch_resp = await client.patch(f"/api/v1/commitments/{cmt_id}", json={
            "title": "Submit comprehensive project review",
            "priority": "medium"
        }, headers=headers)
        assert patch_resp.status_code == 200
        assert patch_resp.json()["title"] == "Submit comprehensive project review"

        # 4. Complete commitment
        comp_resp = await client.post(f"/api/v1/commitments/{cmt_id}/complete", headers=headers)
        assert comp_resp.status_code == 200
        assert comp_resp.json()["status"] == "completed"
        assert comp_resp.json()["completed_at"] is not None
        assert comp_resp.json()["is_active"] is True  # Distinct from deletion

        # 5. Cannot cancel completed commitment
        cancel_resp = await client.post(f"/api/v1/commitments/{cmt_id}/cancel", headers=headers)
        assert cancel_resp.status_code == 400

        # 6. Delete commitment (soft delete)
        del_resp = await client.delete(f"/api/v1/commitments/{cmt_id}", headers=headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True

        # Now 404 in normal get
        get_deleted = await client.get(f"/api/v1/commitments/{cmt_id}", headers=headers)
        assert get_deleted.status_code == 404


@pytest.mark.asyncio
async def test_multi_user_isolation(clean_managers):
    store = clean_managers["store"]
    permissions = clean_managers["permissions"]
    headers_alpha = clean_managers["headers"]
    other_credential = "aurora_cred_" + secrets.token_urlsafe(32)
    other_user = "user_beta"
    async with aiosqlite.connect(store.db_path) as conn:
        await conn.execute(
            "INSERT INTO identity_credentials (token_hash, kind, user_id, device_id, created_at) VALUES (?, 'bearer', ?, ?, ?)",
            (hashlib.sha256(other_credential.encode("ascii")).hexdigest(), other_user, "dev_beta", datetime.now(timezone.utc).isoformat()),
        )
        await conn.commit()
    for permission in (PermissionType.TASKS, PermissionType.COMMITMENTS):
        await permissions.update_permission(permission, PermissionStatus.AUTHORIZED, user_id=other_user)
    headers_beta = {"Authorization": f"Bearer {other_credential}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # User Alpha creates a task and commitment
        t_resp = await client.post("/api/v1/tasks", json={"title": "Alpha's private task"}, headers=headers_alpha)
        assert t_resp.status_code == 201
        alpha_task_id = t_resp.json()["id"]

        c_resp = await client.post("/api/v1/commitments", json={"title": "Alpha's private commitment"}, headers=headers_alpha)
        assert c_resp.status_code == 201
        alpha_cmt_id = c_resp.json()["id"]

        # User Beta lists tasks - must NOT see Alpha's task
        beta_t_list = await client.get("/api/v1/tasks", headers=headers_beta)
        assert beta_t_list.status_code == 200
        assert beta_t_list.json()["total"] == 0
        assert len(beta_t_list.json()["items"]) == 0

        # User Beta lists commitments - must NOT see Alpha's commitment
        beta_c_list = await client.get("/api/v1/commitments", headers=headers_beta)
        assert beta_c_list.status_code == 200
        assert beta_c_list.json()["total"] == 0
        assert len(beta_c_list.json()["items"]) == 0

        # User Beta attempts to GET Alpha's items - returns 404
        assert (await client.get(f"/api/v1/tasks/{alpha_task_id}", headers=headers_beta)).status_code == 404
        assert (await client.get(f"/api/v1/commitments/{alpha_cmt_id}", headers=headers_beta)).status_code == 404

        # User Beta attempts to mutate/delete Alpha's items - returns 404
        assert (await client.post(f"/api/v1/tasks/{alpha_task_id}/complete", headers=headers_beta)).status_code == 404
        assert (await client.delete(f"/api/v1/tasks/{alpha_task_id}", headers=headers_beta)).status_code == 404
        assert (await client.post(f"/api/v1/commitments/{alpha_cmt_id}/complete", headers=headers_beta)).status_code == 404
        assert (await client.delete(f"/api/v1/commitments/{alpha_cmt_id}", headers=headers_beta)).status_code == 404
