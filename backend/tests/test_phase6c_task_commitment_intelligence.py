import asyncio
import hashlib
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.api.v1.endpoints.tasks import get_task_manager_dependency
from app.api.v1.endpoints.commitments import get_commitment_manager_dependency
from app.services.commitments import CommitmentManager, InMemoryCommitmentStore, SQLiteCommitmentStore
from app.services.commitments.models import CommitmentPriority, CommitmentStatus
from app.services.ai.base import AIResponse, BaseAIProvider
from app.services.conversation.agent_loop import deduplicate_client_actions
from app.services.conversation.service import ConversationService
from app.services.conversation.store import InMemoryConversationStore
from app.services.identity.auth import (
    IdentityResolver,
    SQLiteCredentialStore,
    get_credential_store_dependency,
    get_identity_resolver_dependency,
    provision_credential,
)
from app.services.permissions import InMemoryPermissionStore, PermissionManager, PermissionRecord, PermissionStatus, PermissionType, get_permission_manager_dependency
from app.services.tasks import InMemoryTaskStore, SQLiteTaskStore, TaskManager
from app.services.tasks.models import TaskPriority, TaskStatus
from app.services.tools.builtins.task_tool import CompleteTaskTool
from app.services.tools.builtins.commitment_tool import CompleteCommitmentTool
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import ToolCall


@pytest_asyncio.fixture
async def phase6c_managers():
    task_store = InMemoryTaskStore()
    commitment_store = InMemoryCommitmentStore()
    commitments = CommitmentManager(commitment_store)
    tasks = TaskManager(task_store, commitment_manager=commitments)
    return tasks, commitments


@pytest_asyncio.fixture
async def phase6c_api(tmp_path):
    credentials = SQLiteCredentialStore(str(tmp_path / "phase6c-auth.db"))
    secret = await credentials.issue_provisioning()
    provisioned = await provision_credential(secret, credentials)
    resolver = IdentityResolver(credentials)
    tasks, commitments = TaskManager(InMemoryTaskStore()), CommitmentManager(InMemoryCommitmentStore())
    tasks._commitment_manager = commitments
    permission_store = InMemoryPermissionStore()
    permissions = PermissionManager(permission_store)
    for permission in (PermissionType.TASKS, PermissionType.COMMITMENTS):
        await permissions.update_permission(permission, PermissionStatus.AUTHORIZED, user_id=provisioned.user.user_id)
    app.dependency_overrides[get_credential_store_dependency] = lambda: credentials
    app.dependency_overrides[get_identity_resolver_dependency] = lambda: resolver
    app.dependency_overrides[get_task_manager_dependency] = lambda: tasks
    app.dependency_overrides[get_commitment_manager_dependency] = lambda: commitments
    app.dependency_overrides[get_permission_manager_dependency] = lambda: permissions
    yield {"user_id": provisioned.user.user_id, "credential": provisioned.credential, "tasks": tasks, "commitments": commitments, "permissions": permissions}
    for dependency in (get_credential_store_dependency, get_identity_resolver_dependency, get_task_manager_dependency, get_commitment_manager_dependency, get_permission_manager_dependency):
        app.dependency_overrides.pop(dependency, None)


def _auth(credential):
    return {"Authorization": f"Bearer {credential}"}


@pytest.mark.asyncio
async def test_task_update_noop_and_strictly_monotonic_timestamp(phase6c_managers):
    tasks, _ = phase6c_managers
    task = await tasks.create_task("Draft proposal", user_id="user-1", priority=TaskPriority.MEDIUM, tags=["work"])
    unchanged, changed = await tasks.update_task_with_status(task.id, user_id="user-1")
    assert changed is False
    assert unchanged.updated_at == task.updated_at

    unchanged, changed = await tasks.update_task_with_status(task.id, user_id="user-1", title=task.title, tags=["work"])
    assert changed is False
    assert unchanged.updated_at == task.updated_at

    future = datetime.now(timezone.utc) + timedelta(days=2)
    tasks.store._tasks[("user-1", task.id)].updated_at = future
    updated, changed = await tasks.update_task_with_status(task.id, user_id="user-1", priority=TaskPriority.HIGH)
    assert changed is True
    assert updated.updated_at > future
    assert updated.updated_at == future + timedelta(microseconds=1)


@pytest.mark.asyncio
async def test_start_task_uses_existing_lifecycle_and_is_idempotent(phase6c_managers):
    tasks, _ = phase6c_managers
    task = await tasks.create_task("Write tests", user_id="user-1")
    started, changed = await tasks.start_task_with_status(task.id, user_id="user-1")
    assert changed is True and started.status == TaskStatus.IN_PROGRESS
    started_at = started.updated_at
    repeated, changed = await tasks.start_task_with_status(task.id, user_id="user-1")
    assert changed is False and repeated.updated_at == started_at
    completed, changed = await tasks.complete_task_with_status(task.id, user_id="user-1")
    assert changed is True and completed.status == TaskStatus.COMPLETED
    with pytest.raises(ValueError, match="Cannot start completed"):
        await tasks.start_task_with_status(task.id, user_id="user-1")


@pytest.mark.asyncio
async def test_commitment_update_noop_and_monotonic_timestamp(phase6c_managers):
    _, commitments = phase6c_managers
    item = await commitments.create_commitment("Weekly review", user_id="user-1")
    same, changed = await commitments.update_commitment_with_status(item.id, user_id="user-1")
    assert changed is False and same.updated_at == item.updated_at
    same, changed = await commitments.update_commitment_with_status(item.id, user_id="user-1", title=item.title)
    assert changed is False and same.updated_at == item.updated_at

    future = datetime.now(timezone.utc) + timedelta(days=2)
    commitments.store._commitments[("user-1", item.id)].updated_at = future
    updated, changed = await commitments.update_commitment_with_status(item.id, user_id="user-1", priority=CommitmentPriority.HIGH)
    assert changed is True and updated.updated_at == future + timedelta(microseconds=1)


@pytest.mark.asyncio
async def test_concurrent_task_updates_merge_against_persisted_row(phase6c_managers):
    tasks, _ = phase6c_managers
    task = await tasks.create_task("Original", user_id="user-1")
    results = await asyncio.gather(
        tasks.update_task_with_status(task.id, user_id="user-1", title="Renamed"),
        tasks.update_task_with_status(task.id, user_id="user-1", description="Keep this note"),
    )
    persisted = await tasks.get_task(task.id, user_id="user-1")
    assert all(changed for _, changed in results)
    assert persisted.title == "Renamed" and persisted.description == "Keep this note"
    assert persisted.updated_at > task.updated_at


@pytest.mark.asyncio
async def test_concurrent_commitment_updates_merge_against_persisted_row(phase6c_managers):
    _, commitments = phase6c_managers
    item = await commitments.create_commitment("Original", user_id="user-1")
    results = await asyncio.gather(
        commitments.update_commitment_with_status(item.id, user_id="user-1", title="Renamed"),
        commitments.update_commitment_with_status(item.id, user_id="user-1", description="Keep this note"),
    )
    persisted = await commitments.get_commitment(item.id, user_id="user-1")
    assert all(changed for _, changed in results)
    assert persisted.title == "Renamed" and persisted.description == "Keep this note"
    assert persisted.updated_at > item.updated_at


@pytest.mark.asyncio
async def test_sqlite_concurrent_mutations_are_serialized(tmp_path):
    tasks = TaskManager(SQLiteTaskStore(str(tmp_path / "task.sqlite")))
    commitments = CommitmentManager(SQLiteCommitmentStore(str(tmp_path / "commitment.sqlite")))
    task = await tasks.create_task("Original", user_id="user-1")
    item = await commitments.create_commitment("Original", user_id="user-1")
    await asyncio.gather(
        tasks.update_task_with_status(task.id, user_id="user-1", title="SQLite title"),
        tasks.update_task_with_status(task.id, user_id="user-1", description="SQLite note"),
        commitments.update_commitment_with_status(item.id, user_id="user-1", title="SQLite title"),
        commitments.update_commitment_with_status(item.id, user_id="user-1", description="SQLite note"),
    )
    stored_task = await tasks.get_task(task.id, user_id="user-1")
    stored_commitment = await commitments.get_commitment(item.id, user_id="user-1")
    assert (stored_task.title, stored_task.description) == ("SQLite title", "SQLite note")
    assert (stored_commitment.title, stored_commitment.description) == ("SQLite title", "SQLite note")


@pytest.mark.asyncio
async def test_link_relink_unlink_and_cross_user_scope(phase6c_managers):
    tasks, commitments = phase6c_managers
    task = await tasks.create_task("Prepare report", user_id="user-1")
    first = await commitments.create_commitment("Quarterly results", user_id="user-1")
    second = await commitments.create_commitment("Finance review", user_id="user-1")
    linked, changed = await tasks.link_task_commitment(task.id, first.id, user_id="user-1")
    assert changed and linked.source_commitment_id == first.id
    linked_at = linked.updated_at
    same, changed = await tasks.link_task_commitment(task.id, first.id, user_id="user-1")
    assert not changed and same.updated_at == linked_at
    relinked, changed = await tasks.link_task_commitment(task.id, second.id, user_id="user-1")
    assert changed and relinked.source_commitment_id == second.id
    unlinked, changed = await tasks.unlink_task_commitment(task.id, user_id="user-1")
    assert changed and unlinked.source_commitment_id is None
    unlinked_at = unlinked.updated_at
    same, changed = await tasks.unlink_task_commitment(task.id, user_id="user-1")
    assert not changed and same.updated_at == unlinked_at
    with pytest.raises(ValueError, match="does not exist"):
        foreign = await commitments.create_commitment("Foreign", user_id="user-2")
        await tasks.link_task_commitment(task.id, foreign.id, user_id="user-1")
    with pytest.raises(ValueError, match="does not exist"):
        await tasks.link_task_commitment(task.id, "missing-commitment", user_id="user-1")


@pytest.mark.asyncio
async def test_task_schema_upgrade_fresh_and_repeated_initialization(tmp_path):
    old_db = str(tmp_path / "old-task.sqlite")
    async with aiosqlite.connect(old_db) as conn:
        await conn.execute("""CREATE TABLE tasks (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending', priority TEXT NOT NULL DEFAULT 'medium', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, due_at TEXT, completed_at TEXT, tags TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}', is_active INTEGER NOT NULL DEFAULT 1, source_memory_id TEXT
        )""")
        await conn.execute("INSERT INTO tasks(id,user_id,title,created_at,updated_at) VALUES('old','u','Old task','2025-01-01T00:00:00+00:00','2025-01-01T00:00:00+00:00')")
        await conn.commit()
    upgraded = SQLiteTaskStore(old_db)
    await upgraded.initialize()
    await upgraded.initialize()
    async with aiosqlite.connect(old_db) as conn:
        columns = [row[1] for row in await (await conn.execute("PRAGMA table_info(tasks)")).fetchall()]
        count = (await (await conn.execute("SELECT count(*) FROM tasks WHERE id='old'")).fetchone())[0]
        foreign_keys = await (await conn.execute("PRAGMA foreign_key_list(tasks)")).fetchall()
    assert columns.count("source_commitment_id") == 1 and count == 1 and foreign_keys == []

    fresh = SQLiteTaskStore(str(tmp_path / "fresh-task.sqlite"))
    await fresh.initialize()
    await fresh.initialize()
    async with aiosqlite.connect(fresh.db_path) as conn:
        columns = [row[1] for row in await (await conn.execute("PRAGMA table_info(tasks)")).fetchall()]
    assert columns.count("source_commitment_id") == 1


@pytest.mark.asyncio
async def test_client_action_dedup_is_per_entity_and_uses_final_timestamp():
    actions = [
        {"action": "upsert_task", "task_id": "t1", "updated_at": "2026-01-01T00:00:02Z", "action_id": "new"},
        {"action": "upsert_task", "task_id": "t1", "updated_at": "2026-01-01T00:00:01Z", "action_id": "stale"},
        {"action": "upsert_task", "task_id": "t2", "updated_at": "2026-01-01T00:00:01Z", "action_id": "other"},
        {"action": "upsert_commitment", "commitment_id": "t1", "updated_at": "2026-01-01T00:00:01Z", "action_id": "different-domain"},
        {"action": "schedule_local_notification", "reminder_id": "t1", "updated_at": "2026-01-01T00:00:01Z", "action_id": "reminder-domain"},
        {"action": "delete_task", "task_id": "t2", "updated_at": "2026-01-01T00:00:02Z", "action_id": "delete"},
        {"action": "upsert_task", "task_id": "t2", "updated_at": "2026-01-01T00:00:03Z", "action_id": "restore"},
    ]
    result = deduplicate_client_actions(actions)
    assert {(action.get("task_id"), action["action"]) for action in result if action.get("task_id")} == {("t1", "upsert_task"), ("t2", "upsert_task")}
    assert len(result) == 4
    assert next(a for a in result if a.get("task_id") == "t1")["action_id"] == "new"
    assert next(a for a in result if a.get("task_id") == "t2")["action_id"] == "restore"


@pytest.mark.asyncio
async def test_task_agent_tool_requires_server_identity_and_noop_emits_no_action(phase6c_managers):
    from app.services.tools.builtins.task_tool import StartTaskTool, UpdateTaskTool
    tasks, _ = phase6c_managers
    task = await tasks.create_task("Review build", user_id="server-user")
    tool = UpdateTaskTool(tasks)
    no_identity = await tool.execute({"task_id": task.id, "user_id": "server-user", "title": "Attacker-chosen"})
    assert no_identity["status"] == "error"
    noop = await tool.execute({"_user_id": "server-user", "task_id": task.id, "title": task.title})
    assert noop["mutated"] is False and "client_action" not in noop
    start = await StartTaskTool(tasks).execute({"_user_id": "server-user", "task_id": task.id})
    assert start["status_value"] == "in_progress" and start["client_action"]["action"] == "upsert_task"


class _CompleteTaskProvider(BaseAIProvider):
    def __init__(self, task_id):
        self.task_id = task_id
        self.calls = 0

    @property
    def provider_name(self):
        return "phase6c-test"

    async def generate_response(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return AIResponse(
                content="", provider=self.provider_name, model="test", finish_reason="tool_calls",
                tool_calls=[ToolCall(name="complete_task", arguments={"task_id": self.task_id})],
            )
        return AIResponse(content="Task status updated.", provider=self.provider_name, model="test")


class _NoMemoryExtraction:
    async def extract(self, **kwargs):
        return []


async def _paused_task_completion():
    user_id = "phase6c-agent-user"
    manager = TaskManager(InMemoryTaskStore())
    task = await manager.create_task("Prepare release", user_id=user_id)
    registry = ToolRegistry()
    registry.register(CompleteTaskTool(manager))
    permissions = PermissionManager(InMemoryPermissionStore({
        (user_id, PermissionType.TASKS): PermissionRecord(
            permission_type=PermissionType.TASKS, status=PermissionStatus.AUTHORIZED,
            user_id=user_id, source="test",
        )
    }))
    service = ConversationService(
        store=InMemoryConversationStore(), ai_provider=_CompleteTaskProvider(task.id),
        tool_registry=registry, tool_executor=ToolExecutor(registry, permissions),
        candidate_extractor=_NoMemoryExtraction(),
    )
    session, _, _, pending, _ = await service.process_chat("Complete the release task", authenticated_user_id=user_id)
    return user_id, manager, task, service, session, pending


@pytest.mark.asyncio
async def test_medium_risk_task_confirmation_pause_rejection_and_approval():
    user_id, manager, task, service, session, pending = await _paused_task_completion()
    assert pending is not None and session.pending_confirmation is not None
    assert session.active_agent_execution.client_actions == []
    before = await manager.get_task(task.id, user_id=user_id)
    assert before.status == TaskStatus.PENDING and before.updated_at == task.updated_at

    await service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=pending.confirmation_id,
        approved=False,
        authenticated_user_id=user_id,
        execution_id=session.active_agent_execution.execution_id,
    )
    rejected = await manager.get_task(task.id, user_id=user_id)
    assert rejected.status == TaskStatus.PENDING
    assert session.active_agent_execution.client_actions == []

    user_id, manager, task, service, session, pending = await _paused_task_completion()
    assert pending is not None
    await service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=pending.confirmation_id,
        approved=True,
        authenticated_user_id=user_id,
        execution_id=session.active_agent_execution.execution_id,
    )
    completed = await manager.get_task(task.id, user_id=user_id)
    assert completed.status == TaskStatus.COMPLETED
    assert [action["action"] for action in session.active_agent_execution.client_actions] == ["upsert_task"]


class _CompleteCommitmentProvider(BaseAIProvider):
    def __init__(self, commitment_id):
        self.commitment_id = commitment_id
        self.calls = 0

    @property
    def provider_name(self):
        return "phase6c-commitment-test"

    async def generate_response(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return AIResponse(
                content="", provider=self.provider_name, model="test", finish_reason="tool_calls",
                tool_calls=[ToolCall(name="complete_commitment", arguments={"commitment_id": self.commitment_id})],
            )
        return AIResponse(content="Commitment completed.", provider=self.provider_name, model="test")


@pytest.mark.asyncio
async def test_medium_risk_commitment_confirmation_pause_and_approval():
    from app.services.commitments.models import Commitment
    user_id = "phase6c-commitment-agent-user"
    manager = CommitmentManager(InMemoryCommitmentStore())
    commitment = await manager.create_commitment("Deliver the release", user_id=user_id)
    registry = ToolRegistry()
    registry.register(CompleteCommitmentTool(manager))
    permissions = PermissionManager(InMemoryPermissionStore({
        (user_id, PermissionType.COMMITMENTS): PermissionRecord(
            permission_type=PermissionType.COMMITMENTS, status=PermissionStatus.AUTHORIZED,
            user_id=user_id, source="test",
        )
    }))
    service = ConversationService(
        store=InMemoryConversationStore(), ai_provider=_CompleteCommitmentProvider(commitment.id),
        tool_registry=registry, tool_executor=ToolExecutor(registry, permissions),
        candidate_extractor=_NoMemoryExtraction(),
    )
    session, _, _, pending, _ = await service.process_chat(
        "Complete the release commitment", authenticated_user_id=user_id
    )
    assert pending is not None and session.active_agent_execution.client_actions == []
    before = await manager.get_commitment(commitment.id, user_id=user_id)
    assert before.status == CommitmentStatus.ACTIVE and before.updated_at == commitment.updated_at
    execution_id = session.active_agent_execution.execution_id
    await service.confirm_tool_action(
        conversation_id=session.conversation_id,
        confirmation_id=pending.confirmation_id,
        approved=True,
        authenticated_user_id=user_id,
        execution_id=execution_id,
    )
    after = await manager.get_commitment(commitment.id, user_id=user_id)
    assert after.status == CommitmentStatus.COMPLETED
    assert [action["action"] for action in session.active_agent_execution.client_actions] == ["upsert_commitment"]


@pytest.mark.asyncio
async def test_authenticated_start_endpoint_and_permission_boundary(phase6c_api):
    task = await phase6c_api["tasks"].create_task("Ship feature", user_id=phase6c_api["user_id"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing_auth = await client.post(f"/api/v1/tasks/{task.id}/start")
        started = await client.post(f"/api/v1/tasks/{task.id}/start", headers=_auth(phase6c_api["credential"]))
    assert missing_auth.status_code == 401
    assert started.status_code == 200 and started.json()["status"] == "in_progress"
    assert started.json()["user_id"] == phase6c_api["user_id"]

    await phase6c_api["permissions"].update_permission(PermissionType.TASKS, PermissionStatus.DENIED, user_id=phase6c_api["user_id"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post(f"/api/v1/tasks/{task.id}/start", headers=_auth(phase6c_api["credential"]))
    assert denied.status_code == 403


def test_ios_client_action_decoder_and_viewmodels_keep_phase6b_compatibility():
    from pathlib import Path
    root = Path(__file__).parents[2] / "ios" / "ProjectAurora"
    models = (root / "Models" / "APIModels.swift").read_text()
    task_vm = (root / "ViewModels" / "TaskViewModel.swift").read_text()
    commitment_vm = (root / "ViewModels" / "CommitmentViewModel.swift").read_text()
    proactive_models = (root / "Models" / "ProactiveModels.swift").read_text()
    proactive_view = (root / "Views" / "ProactiveView.swift").read_text()
    api_client = (root / "Services" / "AuroraAPIClient.swift").read_text()
    assert "decodeIfPresent(String.self, forKey: .entityType)" in models
    assert "decodeIfPresent(String.self, forKey: .taskId)" in models
    assert "decodeIfPresent(String.self, forKey: .reminderId)" in models
    assert "updatedAt > tasks[index].updatedAt" in task_vm
    assert "updatedAt > commitments[index].updatedAt" in commitment_vm
    assert "executionId = \"execution_id\"" in proactive_models
    assert "@State private var pendingExecutionId: String?" in proactive_view
    assert "executionId: executionId" in proactive_view
    assert "taskViewModel.applyAssistantAction(action)" in proactive_view
    assert "commitmentViewModel.applyAssistantAction(action)" in proactive_view
    assert "let (data, response) = try await performRequest(request)" in api_client
    assert "path.hasSuffix(\"/accept\")" in api_client
    assert "X-User-ID" not in api_client
