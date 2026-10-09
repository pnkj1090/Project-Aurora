import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.commitments import CommitmentManager, InMemoryCommitmentStore, get_commitment_manager_dependency
from app.services.conversation.models import AgentExecutionState, AgentExecutionStatus
from app.services.conversation.service import ConversationIdentityError, ConversationService
from app.services.conversation import get_conversation_service
from app.services.conversation.session import ConversationSession
from app.services.conversation.store import InMemoryConversationStore
from app.services.ai.base import AIResponse, BaseAIProvider, MessageRole
from app.services.identity.auth import (
    IdentityResolver,
    SQLiteCredentialStore,
    get_credential_store_dependency,
    get_identity_resolver_dependency,
    provision_credential,
)
from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions import (
    InMemoryPermissionStore,
    PermissionManager,
    PermissionStatus,
    PermissionType,
    get_permission_manager_dependency,
)
from app.services.tasks import InMemoryTaskStore, TaskManager, Task, TaskStatus, get_task_manager_dependency
from app.services.tools.base import ToolRiskLevel
from app.services.tools.registry import ToolRegistry
from app.services.tools.executor import ToolExecutor
from app.services.tools.builtins.task_tool import CompleteTaskTool
from app.services.tools.schemas import PendingConfirmation, ToolCall
from app.services.proactive.models import ProactiveCandidate, ProactiveStatus, ProactiveTrigger, ProactiveAction, ProactivePriority
from app.services.proactive.store import InMemoryProactiveStore
from app.services.proactive.executor import ProactiveActionExecutor, get_proactive_action_executor
from app.services.proactive.manager import ProactiveManager
from starlette.requests import Request


@pytest_asyncio.fixture
async def auth_env(tmp_path):
    store = SQLiteCredentialStore(str(tmp_path / "auth.db"))
    secret = await store.issue_provisioning()
    provisioned = await provision_credential(secret, store)
    resolver = IdentityResolver(store)

    task_manager = TaskManager(store=InMemoryTaskStore())
    commitment_manager = CommitmentManager(store=InMemoryCommitmentStore())
    permissions = PermissionManager(InMemoryPermissionStore())
    await permissions.update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=DEFAULT_LOCAL_USER_ID)
    await permissions.update_permission(PermissionType.COMMITMENTS, PermissionStatus.AUTHORIZED, user_id=DEFAULT_LOCAL_USER_ID)

    app.dependency_overrides[get_credential_store_dependency] = lambda: store
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: resolver
    app.dependency_overrides[get_task_manager_dependency] = lambda: task_manager
    app.dependency_overrides[get_commitment_manager_dependency] = lambda: commitment_manager
    app.dependency_overrides[get_permission_manager_dependency] = lambda: permissions
    yield {
        "store": store, "secret": secret, "credential": provisioned.credential,
        "user": provisioned.user, "device": provisioned.device,
        "resolver": resolver, "tasks": task_manager, "commitments": commitment_manager,
        "permissions": permissions,
    }
    for dep in (
        get_credential_store_dependency, get_identity_resolver_dependency,
        get_task_manager_dependency, get_commitment_manager_dependency,
        get_permission_manager_dependency,
    ):
        app.dependency_overrides.pop(dep, None)


def bearer(credential):
    return {"Authorization": f"Bearer {credential}"}


@pytest.mark.asyncio
async def test_provision_and_resolve_server_assigned_user_and_device(auth_env):
    scope = {"type": "http", "headers": [(b"authorization", f"Bearer {auth_env['credential']}".encode("ascii"))], "method": "GET", "path": "/", "query_string": b"", "server": ("test", 80), "client": ("test", 1), "scheme": "http"}
    principal = await auth_env["resolver"].resolve(Request(scope))
    assert principal.user.user_id == DEFAULT_LOCAL_USER_ID
    assert principal.device.device_id == auth_env["device"].device_id
    async with aiosqlite.connect(auth_env["store"].db_path) as conn:
        rows = await (await conn.execute("SELECT token_hash FROM identity_credentials")).fetchall()
    stored_hashes = {row[0] for row in rows}
    assert hashlib.sha256(auth_env["secret"].encode("ascii")).hexdigest() in stored_hashes
    assert hashlib.sha256(auth_env["credential"].encode("ascii")).hexdigest() in stored_hashes
    assert auth_env["secret"] not in stored_hashes
    assert auth_env["credential"] not in stored_hashes


@pytest.mark.asyncio
async def test_identity_schema_initialization_is_idempotent_and_non_destructive(tmp_path):
    db_path = str(tmp_path / "existing.db")
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE existing_rows (value TEXT NOT NULL)")
        await conn.execute("INSERT INTO existing_rows VALUES ('preserve')")
        await conn.commit()
    store = SQLiteCredentialStore(db_path)
    await store.initialize()
    await store.initialize()
    async with aiosqlite.connect(db_path) as conn:
        tables = {row[0] for row in await (await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()}
        count = (await (await conn.execute("SELECT COUNT(*) FROM existing_rows")).fetchone())[0]
        columns = [row[1] for row in await (await conn.execute("PRAGMA table_info(identity_credentials)")).fetchall()]
    assert "identity_credentials" in tables
    assert count == 1
    assert columns == ["token_hash", "kind", "user_id", "device_id", "created_at", "expires_at", "consumed_at", "revoked_at"]


@pytest.mark.asyncio
async def test_provisioning_single_use_and_secret_not_in_error_or_logs(auth_env, caplog):
    secret = await auth_env["store"].issue_provisioning()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level(logging.INFO):
            first = await client.post("/api/v1/auth/provision", json={"provisioning_secret": secret})
            replay = await client.post("/api/v1/auth/provision", json={"provisioning_secret": secret})
    assert first.status_code == 201
    assert replay.status_code == 401
    assert secret not in replay.text
    assert secret not in caplog.text
    assert first.json()["user"]["user_id"] == DEFAULT_LOCAL_USER_ID
    assert first.json()["device"]["device_id"] != auth_env["device"].device_id


@pytest.mark.asyncio
async def test_expired_malformed_unknown_and_concurrently_reused_provisioning(auth_env):
    store = auth_env["store"]
    expired = await store.issue_provisioning()
    expired_hash = hashlib.sha256(expired.encode("ascii")).hexdigest()
    async with aiosqlite.connect(store.db_path) as conn:
        await conn.execute(
            "UPDATE identity_credentials SET expires_at = ? WHERE token_hash = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), expired_hash),
        )
        await conn.commit()
    for value in (expired, "aurora_prov_bad", "aurora_prov_" + "A" * 43):
        if value == expired:
            with pytest.raises(Exception) as err:
                await provision_credential(value, store)
            assert getattr(err.value, "status_code", None) == 401
        else:
            with pytest.raises(Exception) as err:
                await provision_credential(value, store)
            assert getattr(err.value, "status_code", None) == 401

    replay_secret = await store.issue_provisioning()
    digest = hashlib.sha256(replay_secret.encode("ascii")).hexdigest()
    results = await asyncio.gather(
        store.redeem_provisioning(digest), store.redeem_provisioning(digest), return_exceptions=True
    )
    assert sum(result is not None for result in results) == 1


@pytest.mark.asyncio
async def test_protected_routes_fail_closed_and_reject_identity_overrides(auth_env):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/api/v1/tasks", json={"title": "No auth"})).status_code == 401
        assert (await client.post("/api/v1/tasks", json={"title": "Bad"}, headers=bearer("aurora_cred_bad"))).status_code == 401
        assert (await client.post("/api/v1/tasks", json={"title": "Unknown"}, headers=bearer("aurora_cred_" + "C" * 43))).status_code == 401
        spoofed = await client.post("/api/v1/tasks", json={"title": "Spoof"}, headers={**bearer(auth_env["credential"]), "X-User-ID": "victim"})
        assert spoofed.status_code == 400
        body_spoof = await client.post("/api/v1/tasks", json={"title": "Spoof", "user_id": "victim"}, headers=bearer(auth_env["credential"]))
        assert body_spoof.status_code == 422
        query_spoof = await client.get("/api/v1/tasks?user_id=victim", headers=bearer(auth_env["credential"]))
        assert query_spoof.status_code == 400
        assert await auth_env["tasks"].count_tasks(user_id=DEFAULT_LOCAL_USER_ID) == 0


@pytest.mark.asyncio
async def test_permission_checks_use_resolved_identity_and_cross_user_is_404(auth_env):
    other_user = "usr_other_identity"
    credential = "aurora_cred_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(credential.encode("ascii")).hexdigest()
    async with aiosqlite.connect(auth_env["store"].db_path) as conn:
        await conn.execute(
            "INSERT INTO identity_credentials (token_hash, kind, user_id, device_id, created_at) VALUES (?, 'bearer', ?, ?, ?)",
            (token_hash, other_user, "dev_test_other", datetime.now(timezone.utc).isoformat()),
        )
        await conn.commit()
    task = await auth_env["tasks"].create_task("Private", user_id=DEFAULT_LOCAL_USER_ID)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(f"/api/v1/tasks/{task.id}", headers=bearer(credential))).status_code == 404
        denied = await client.post("/api/v1/tasks", json={"title": "No permission"}, headers=bearer(credential))
        assert denied.status_code == 403
        await auth_env["permissions"].update_permission(PermissionType.TASKS, PermissionStatus.AUTHORIZED, user_id=other_user)
        foreign_mutation = await client.post(f"/api/v1/tasks/{task.id}/complete", headers=bearer(credential))
        assert foreign_mutation.status_code == 404
        assert await auth_env["tasks"].count_tasks(user_id=other_user) == 0


@pytest.mark.asyncio
async def test_revocation_immediately_blocks_next_request(auth_env):
    assert await auth_env["store"].revoke_device(auth_env["device"].device_id) == 1
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/tasks", headers=bearer(auth_env["credential"]))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_chat_binds_session_and_rejects_other_user_resume():
    store = InMemoryConversationStore()
    service = ConversationService(store=store)
    async def execute_turn(session, user_text, model=None):
        message = session.add_message(MessageRole.ASSISTANT, "ok")
        return message, AIResponse(content="ok", provider="test", model="test"), None, []
    service.agent_loop.execute_turn = execute_turn
    created, *_ = await service.process_chat("hello", authenticated_user_id="usr_authenticated")
    assert created.user_id == "usr_authenticated"
    assert (await store.get(created.conversation_id)).user_id == "usr_authenticated"
    session = ConversationSession(conversation_id="owned", metadata={"user_id": "usr_owner"})
    await store.save(session)
    with pytest.raises(ConversationIdentityError):
        await service.process_chat("hello", "owned", authenticated_user_id="usr_attacker")


@pytest.mark.asyncio
async def test_confirmation_execution_correlation_rejects_mismatch():
    store = InMemoryConversationStore()
    service = ConversationService(store=store)
    call = ToolCall(id="tc1", name="task_update", arguments={})
    pending = PendingConfirmation(confirmation_id="conf1", tool_call=call, risk_level=ToolRiskLevel.LOW, description="confirm", agent_execution_id="exec1")
    execution = AgentExecutionState(
        conversation_id="conv1", user_id="usr_owner", execution_id="exec1",
        execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        pending_confirmation_id="conf1", paused_tool_call=call,
    )
    session = ConversationSession(conversation_id="conv1", metadata={"user_id": "usr_owner"}, pending_confirmation=pending, active_agent_execution=execution)
    await store.save(session)
    with pytest.raises(ConversationIdentityError):
        await service.confirm_tool_action("conv1", "conf1", True, authenticated_user_id="usr_owner", execution_id="exec_other")
    with pytest.raises(ConversationIdentityError):
        await service.confirm_tool_action("conv1", "conf1", True, authenticated_user_id="usr_other", execution_id="exec1")


def _pending_confirmation_session(user_id: str = DEFAULT_LOCAL_USER_ID):
    call = ToolCall(id="tool-call-1", name="task_update", arguments={"task_id": "task-1"})
    pending = PendingConfirmation(
        confirmation_id="confirm-api-1", tool_call=call,
        risk_level=ToolRiskLevel.LOW, description="Update task",
        agent_execution_id="execution-api-1",
    )
    execution = AgentExecutionState(
        conversation_id="conversation-api-1", user_id=user_id,
        execution_id="execution-api-1",
        execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        pending_confirmation_id="confirm-api-1", paused_tool_call=call,
    )
    return ConversationSession(
        conversation_id="conversation-api-1", metadata={"user_id": user_id},
        pending_confirmation=pending, active_agent_execution=execution,
    )


@pytest_asyncio.fixture
async def confirmation_api(auth_env):
    store = InMemoryConversationStore()
    service = ConversationService(store=store)
    session = _pending_confirmation_session()
    await store.save(session)
    tool_executions = []

    async def resume_execution(session, confirmation_id, approved, model=None):
        tool_executions.append((confirmation_id, approved))
        session.pending_confirmation = None
        session.active_agent_execution.execution_status = AgentExecutionStatus.COMPLETED
        message = session.add_message(MessageRole.ASSISTANT, "Confirmed")
        return message, AIResponse(content="Confirmed", provider="test", model="test"), []

    service.agent_loop.resume_execution = resume_execution
    lookups = []
    original_confirm = service.confirm_tool_action

    async def tracked_confirm(**kwargs):
        lookups.append(kwargs)
        return await original_confirm(**kwargs)

    service.confirm_tool_action = tracked_confirm
    app.dependency_overrides[get_conversation_service] = lambda: service
    yield {"service": service, "session": session, "executions": tool_executions, "lookups": lookups}
    app.dependency_overrides.pop(get_conversation_service, None)


@pytest.mark.asyncio
async def test_confirm_api_requires_execution_id_before_confirmation_lookup(confirmation_api, auth_env):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/confirm",
            headers=bearer(auth_env["credential"]),
            json={"conversation_id": "conversation-api-1", "confirmation_id": "confirm-api-1", "approved": True},
        )
    assert response.status_code == 422
    assert confirmation_api["lookups"] == []
    assert confirmation_api["executions"] == []
    assert confirmation_api["session"].pending_confirmation is not None
    assert response.json().get("client_actions") is None


@pytest.mark.asyncio
async def test_confirm_api_wrong_execution_id_is_404_without_execution_or_actions(confirmation_api, auth_env):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/confirm", headers=bearer(auth_env["credential"]),
            json={"conversation_id": "conversation-api-1", "confirmation_id": "confirm-api-1", "execution_id": "wrong-exec", "approved": True},
        )
    assert response.status_code == 404
    assert confirmation_api["executions"] == []
    assert confirmation_api["session"].pending_confirmation is not None
    assert "client_actions" not in response.json()


@pytest.mark.asyncio
async def test_confirm_api_matching_execution_succeeds(confirmation_api, auth_env):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/confirm", headers=bearer(auth_env["credential"]),
            json={"conversation_id": "conversation-api-1", "confirmation_id": "confirm-api-1", "execution_id": "execution-api-1", "approved": True},
        )
    assert response.status_code == 200
    assert confirmation_api["executions"] == [("confirm-api-1", True)]
    assert confirmation_api["session"].pending_confirmation is None


@pytest.mark.asyncio
async def test_confirm_api_cross_user_is_404_without_execution_or_actions(confirmation_api, auth_env):
    other_user = "usr_confirmation_attacker"
    credential = "aurora_cred_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(credential.encode("ascii")).hexdigest()
    async with aiosqlite.connect(auth_env["store"].db_path) as conn:
        await conn.execute(
            "INSERT INTO identity_credentials (token_hash, kind, user_id, device_id, created_at) VALUES (?, 'bearer', ?, ?, ?)",
            (token_hash, other_user, "dev_confirmation_attacker", datetime.now(timezone.utc).isoformat()),
        )
        await conn.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/confirm", headers=bearer(credential),
            json={"conversation_id": "conversation-api-1", "confirmation_id": "confirm-api-1", "execution_id": "execution-api-1", "approved": True},
        )
    assert response.status_code == 404
    assert confirmation_api["executions"] == []
    assert confirmation_api["session"].pending_confirmation is not None
    assert "client_actions" not in response.json()


def test_protected_api_files_have_no_default_identity_fallback():
    from pathlib import Path
    root = Path(__file__).parents[1] / "app" / "api" / "v1" / "endpoints"
    for name in ("tasks.py", "commitments.py", "chat.py"):
        source = (root / name).read_text()
        assert "default_identity_manager" not in source
        assert "DEFAULT_LOCAL_USER_ID" not in source


class _ProactiveConfirmationProvider(BaseAIProvider):
    @property
    def provider_name(self):
        return "proactive-confirmation-test"

    async def generate_response(self, messages, tools=None, **kwargs):
        return AIResponse(content="Confirmed.", provider=self.provider_name, model="test")


class _NoCandidateExtraction:
    async def extract(self, **kwargs):
        return []


@pytest.mark.asyncio
async def test_proactive_task_completion_requires_authenticated_correlated_confirmation(auth_env):
    user_id = auth_env["user"].user_id
    task = await auth_env["tasks"].create_task("Proactive completion", user_id=user_id)
    candidate_store = InMemoryProactiveStore()
    candidate = ProactiveCandidate(
        user_id=user_id, source="tasks", trigger=ProactiveTrigger.TASK_DUE,
        action=ProactiveAction.COMPLETE_TASK, priority=ProactivePriority.MEDIUM,
        status=ProactiveStatus.ELIGIBLE, title="Complete proactive task",
        message="The task may be completed.", reference_id=task.id,
        action_payload={"task_id": task.id},
    )
    await candidate_store.add(candidate)

    registry = ToolRegistry()
    registry.register(CompleteTaskTool(auth_env["tasks"]))
    tool_executor = ToolExecutor(registry, auth_env["permissions"])
    conversation_store = InMemoryConversationStore()
    conversation_service = ConversationService(
        store=conversation_store, ai_provider=_ProactiveConfirmationProvider(),
        tool_registry=registry, tool_executor=tool_executor,
        candidate_extractor=_NoCandidateExtraction(),
    )
    proactive_manager = ProactiveManager(store=candidate_store, permission_manager=auth_env["permissions"])
    proactive_executor = ProactiveActionExecutor(
        store=candidate_store, task_manager=auth_env["tasks"],
        permission_manager=auth_env["permissions"], tool_executor=tool_executor,
        conversation_service=conversation_service,
    )
    app.dependency_overrides[get_conversation_service] = lambda: conversation_service
    app.dependency_overrides[get_proactive_action_executor] = lambda: proactive_executor
    manager_module = __import__("app.services.proactive.manager", fromlist=["_default_proactive_manager"])
    original_manager = manager_module._default_proactive_manager
    manager_module._default_proactive_manager = proactive_manager
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing_auth = await client.post(
                f"/api/v1/proactive/candidates/{candidate.candidate_id}/accept",
                headers={"X-User-ID": user_id}, json={},
            )
            assert missing_auth.status_code == 401
            assert (await candidate_store.get(candidate.candidate_id, user_id)).status == ProactiveStatus.ELIGIBLE

            other_user = "usr_proactive_confirmation_attacker"
            attacker_credential = "aurora_cred_" + secrets.token_urlsafe(32)
            attacker_hash = hashlib.sha256(attacker_credential.encode("ascii")).hexdigest()
            async with aiosqlite.connect(auth_env["store"].db_path) as conn:
                await conn.execute(
                    "INSERT INTO identity_credentials (token_hash, kind, user_id, device_id, created_at) VALUES (?, 'bearer', ?, ?, ?)",
                    (attacker_hash, other_user, "dev_proactive_confirmation_attacker", datetime.now(timezone.utc).isoformat()),
                )
                await conn.commit()
            cross_user_accept = await client.post(
                f"/api/v1/proactive/candidates/{candidate.candidate_id}/accept",
                headers=bearer(attacker_credential), json={},
            )
            assert cross_user_accept.status_code == 404
            assert (await candidate_store.get(candidate.candidate_id, user_id)).status == ProactiveStatus.ELIGIBLE

            accepted = await client.post(
                f"/api/v1/proactive/candidates/{candidate.candidate_id}/accept",
                headers=bearer(auth_env["credential"]), json={},
            )
            assert accepted.status_code == 200
            pending = accepted.json()
            assert pending["status"] == "accepted" and pending["confirmation_required"] is True
            assert pending["conversation_id"] and pending["execution_id"]
            assert pending["pending_confirmation"]["confirmation_id"]
            unchanged = await auth_env["tasks"].get_task(task.id, user_id=user_id)
            assert unchanged.status == TaskStatus.PENDING and unchanged.updated_at == task.updated_at

            conversation_id = pending["conversation_id"]
            confirmation_id = pending["pending_confirmation"]["confirmation_id"]
            execution_id = pending["execution_id"]
            bad_execution = await client.post(
                "/api/v1/chat/confirm", headers=bearer(auth_env["credential"]),
                json={"conversation_id": conversation_id, "confirmation_id": confirmation_id,
                      "execution_id": "wrong-execution", "approved": True},
            )
            assert bad_execution.status_code == 404
            assert "client_actions" not in bad_execution.json()
            assert (await auth_env["tasks"].get_task(task.id, user_id=user_id)).status == TaskStatus.PENDING

            cross_user = await client.post(
                "/api/v1/chat/confirm", headers=bearer(attacker_credential),
                json={"conversation_id": conversation_id, "confirmation_id": confirmation_id,
                      "execution_id": execution_id, "approved": True},
            )
            assert cross_user.status_code == 404
            assert (await auth_env["tasks"].get_task(task.id, user_id=user_id)).status == TaskStatus.PENDING

            forged_args = await client.post(
                "/api/v1/chat/confirm", headers=bearer(auth_env["credential"]),
                json={"conversation_id": conversation_id, "confirmation_id": confirmation_id,
                      "execution_id": execution_id, "approved": True, "task_id": "attacker-choice"},
            )
            assert forged_args.status_code == 422
            assert (await auth_env["tasks"].get_task(task.id, user_id=user_id)).status == TaskStatus.PENDING

            approved = await client.post(
                "/api/v1/chat/confirm", headers=bearer(auth_env["credential"]),
                json={"conversation_id": conversation_id, "confirmation_id": confirmation_id,
                      "execution_id": execution_id, "approved": True},
            )
            assert approved.status_code == 200
            actions = approved.json()["client_actions"]
            assert [item["action"] for item in actions] == ["upsert_task"]
            assert actions[0]["task_id"] == task.id
            assert (await auth_env["tasks"].get_task(task.id, user_id=user_id)).status == TaskStatus.COMPLETED
            assert (await candidate_store.get(candidate.candidate_id, user_id)).status == ProactiveStatus.EXECUTED

            replay = await client.post(
                "/api/v1/chat/confirm", headers=bearer(auth_env["credential"]),
                json={"conversation_id": conversation_id, "confirmation_id": confirmation_id,
                      "execution_id": execution_id, "approved": True},
            )
            assert replay.status_code == 404
            assert (await auth_env["tasks"].get_task(task.id, user_id=user_id)).updated_at > task.updated_at
    finally:
        manager_module._default_proactive_manager = original_manager
        app.dependency_overrides.pop(get_conversation_service, None)
        app.dependency_overrides.pop(get_proactive_action_executor, None)


@pytest.mark.asyncio
async def test_dev_bootstrap_success_and_authenticates_protected_endpoints(auth_env):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Initial call without credentials fails
        unauth = await client.get("/api/v1/tasks")
        assert unauth.status_code == 401

        # Dev bootstrap generates and returns credential
        resp = await client.post("/api/v1/auth/dev-bootstrap")
        assert resp.status_code == 201
        data = resp.json()
        assert "credential" in data
        assert data["credential"].startswith("aurora_cred_")
        assert data["user"]["user_id"] == DEFAULT_LOCAL_USER_ID
        assert data["device"]["device_id"].startswith("dev_")

        # Returned credential successfully accesses protected endpoints
        auth_header = {"Authorization": f"Bearer {data['credential']}"}
        authed = await client.get("/api/v1/tasks", headers=auth_header)
        assert authed.status_code == 200


@pytest.mark.asyncio
async def test_dev_bootstrap_fails_outside_development(auth_env, monkeypatch):
    from app.core.config import settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Production environment -> 404
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        resp_prod = await client.post("/api/v1/auth/dev-bootstrap")
        assert resp_prod.status_code == 404

        # Debug false -> 404
        monkeypatch.setattr(settings, "ENVIRONMENT", "development")
        monkeypatch.setattr(settings, "DEBUG", False)
        resp_nodebug = await client.post("/api/v1/auth/dev-bootstrap")
        assert resp_nodebug.status_code == 404


@pytest.mark.asyncio
async def test_dev_bootstrap_rejected_for_non_loopback(auth_env):
    # Remote IP e.g. from LAN -> 403 Forbidden
    transport = ASGITransport(app=app, client=("192.168.1.55", 54321))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/auth/dev-bootstrap")
        assert resp.status_code == 403
        assert "restricted to local loopback" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dev_bootstrap_does_not_leak_provider_key_or_secrets(auth_env, caplog):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with caplog.at_level(logging.DEBUG):
            resp = await client.post("/api/v1/auth/dev-bootstrap")
            assert resp.status_code == 201
            body = resp.text
            assert "GEMINI" not in body
            assert "OPENAI" not in body
            assert "aurora_prov_" not in body
            for record in caplog.records:
                assert "aurora_prov_" not in record.message


