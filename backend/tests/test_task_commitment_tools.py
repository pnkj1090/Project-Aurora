import pytest
from app.services.tasks import InMemoryTaskStore, TaskManager
from app.services.commitments import InMemoryCommitmentStore, CommitmentManager
from app.services.tools.base import ToolRiskLevel
from app.services.tools.schemas import ToolCall
from app.services.tools.registry import ToolRegistry, default_tool_registry
from app.services.tools.executor import ToolExecutor, ToolConfirmationRequired
from app.services.permissions import InMemoryPermissionStore, PermissionManager, PermissionRecord, PermissionStatus, PermissionType
from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.tools.builtins.task_tool import (
    CreateTaskTool,
    ListTasksTool,
    StartTaskTool,
    CompleteTaskTool,
    CancelTaskTool,
)
from app.services.tools.builtins.commitment_tool import (
    CreateCommitmentTool,
    ListCommitmentsTool,
    CompleteCommitmentTool,
    CancelCommitmentTool,
)


@pytest.fixture
def task_tool_env():
    store = InMemoryTaskStore()
    manager = TaskManager(store=store)

    create_tool = CreateTaskTool(task_manager=manager)
    list_tool = ListTasksTool(task_manager=manager)
    complete_tool = CompleteTaskTool(task_manager=manager)
    start_tool = StartTaskTool(task_manager=manager)
    cancel_tool = CancelTaskTool(task_manager=manager)

    registry = ToolRegistry()
    registry.register(create_tool)
    registry.register(list_tool)
    registry.register(complete_tool)
    registry.register(start_tool)
    registry.register(cancel_tool)

    permissions = PermissionManager(InMemoryPermissionStore({
        (DEFAULT_LOCAL_USER_ID, PermissionType.TASKS): PermissionRecord(
            permission_type=PermissionType.TASKS, status=PermissionStatus.AUTHORIZED, user_id=DEFAULT_LOCAL_USER_ID, source="test"
        )
    }))
    executor = ToolExecutor(registry=registry, permission_manager=permissions)
    return {
        "manager": manager,
        "create_tool": create_tool,
        "list_tool": list_tool,
        "complete_tool": complete_tool,
        "start_tool": start_tool,
        "cancel_tool": cancel_tool,
        "executor": executor,
    }


@pytest.fixture
def commitment_tool_env():
    store = InMemoryCommitmentStore()
    manager = CommitmentManager(store=store)

    create_tool = CreateCommitmentTool(commitment_manager=manager)
    list_tool = ListCommitmentsTool(commitment_manager=manager)
    complete_tool = CompleteCommitmentTool(commitment_manager=manager)
    cancel_tool = CancelCommitmentTool(commitment_manager=manager)

    registry = ToolRegistry()
    registry.register(create_tool)
    registry.register(list_tool)
    registry.register(complete_tool)
    registry.register(cancel_tool)

    permissions = PermissionManager(InMemoryPermissionStore({
        (DEFAULT_LOCAL_USER_ID, PermissionType.COMMITMENTS): PermissionRecord(
            permission_type=PermissionType.COMMITMENTS, status=PermissionStatus.AUTHORIZED, user_id=DEFAULT_LOCAL_USER_ID, source="test"
        )
    }))
    executor = ToolExecutor(registry=registry, permission_manager=permissions)
    return {
        "manager": manager,
        "create_tool": create_tool,
        "list_tool": list_tool,
        "complete_tool": complete_tool,
        "cancel_tool": cancel_tool,
        "executor": executor,
    }


def test_default_tool_registry_includes_task_and_commitment_tools():
    tools = default_tool_registry.list_tools()
    names = {t.definition.name for t in tools}

    assert "create_task" in names
    assert "list_tasks" in names
    assert "start_task" in names
    assert "complete_task" in names
    assert "cancel_task" in names

    assert "create_commitment" in names
    assert "list_commitments" in names
    assert "complete_commitment" in names
    assert "cancel_commitment" in names


def test_task_and_commitment_tool_risk_levels(task_tool_env, commitment_tool_env):
    # Tasks
    assert task_tool_env["create_tool"].definition.risk_level == ToolRiskLevel.LOW
    assert task_tool_env["create_tool"].definition.requires_confirmation is False

    assert task_tool_env["list_tool"].definition.risk_level == ToolRiskLevel.LOW
    assert task_tool_env["list_tool"].definition.requires_confirmation is False

    assert task_tool_env["complete_tool"].definition.risk_level == ToolRiskLevel.MEDIUM
    assert task_tool_env["complete_tool"].definition.requires_confirmation is True

    assert task_tool_env["cancel_tool"].definition.risk_level == ToolRiskLevel.MEDIUM
    assert task_tool_env["cancel_tool"].definition.requires_confirmation is True

    # Commitments
    assert commitment_tool_env["create_tool"].definition.risk_level == ToolRiskLevel.LOW
    assert commitment_tool_env["create_tool"].definition.requires_confirmation is False

    assert commitment_tool_env["list_tool"].definition.risk_level == ToolRiskLevel.LOW
    assert commitment_tool_env["list_tool"].definition.requires_confirmation is False

    assert commitment_tool_env["complete_tool"].definition.risk_level == ToolRiskLevel.MEDIUM
    assert commitment_tool_env["complete_tool"].definition.requires_confirmation is True

    assert commitment_tool_env["cancel_tool"].definition.risk_level == ToolRiskLevel.MEDIUM
    assert commitment_tool_env["cancel_tool"].definition.requires_confirmation is True


@pytest.mark.asyncio
async def test_task_tools_execution_and_confirmation(task_tool_env):
    create_tool = task_tool_env["create_tool"]
    list_tool = task_tool_env["list_tool"]
    complete_tool = task_tool_env["complete_tool"]
    executor = task_tool_env["executor"]
    manager = task_tool_env["manager"]

    # 1. Create task
    res = await create_tool.execute({"_user_id": DEFAULT_LOCAL_USER_ID, "title": "Design user interface", "priority": "high"})
    assert res["status"] == "success"
    task_id = res["task_id"]

    # 2. List tasks
    list_res = await list_tool.execute({"_user_id": DEFAULT_LOCAL_USER_ID})
    assert list_res["status"] == "success"
    assert list_res["count"] == 1
    assert list_res["tasks"][0]["id"] == task_id

    # 3. Complete task only after approval
    task_before = await manager.get_task(task_id)
    complete_call = ToolCall(name="complete_task", arguments={"task_id": task_id})
    with pytest.raises(ToolConfirmationRequired):
        await executor.execute(complete_call, user_id=DEFAULT_LOCAL_USER_ID)
    assert (await manager.get_task(task_id)).updated_at == task_before.updated_at
    comp_res = await executor.execute(complete_call, is_pre_approved=True, user_id=DEFAULT_LOCAL_USER_ID)
    assert comp_res.success is True
    assert comp_res.output["status_value"] == "completed"
    assert "client_action" in comp_res.output

    # 4. Cancel task tool through ToolExecutor
    task2 = await manager.create_task(title="Task to cancel")
    call = ToolCall(name="cancel_task", arguments={"task_id": task2.id})

    # Must raise confirmation required when not pre-approved
    with pytest.raises(ToolConfirmationRequired) as exc_info:
        await executor.execute(call, is_pre_approved=False, user_id=DEFAULT_LOCAL_USER_ID)
    assert exc_info.value.pending.risk_level == ToolRiskLevel.MEDIUM
    assert task2.id in exc_info.value.pending.description

    # Approved execution succeeds
    exec_res = await executor.execute(call, is_pre_approved=True, user_id=DEFAULT_LOCAL_USER_ID)
    assert exec_res.success is True
    assert exec_res.output["status_value"] == "cancelled"


@pytest.mark.asyncio
async def test_commitment_tools_execution_and_confirmation(commitment_tool_env):
    create_tool = commitment_tool_env["create_tool"]
    list_tool = commitment_tool_env["list_tool"]
    complete_tool = commitment_tool_env["complete_tool"]
    executor = commitment_tool_env["executor"]
    manager = commitment_tool_env["manager"]

    # 1. Create commitment
    res = await create_tool.execute({"_user_id": DEFAULT_LOCAL_USER_ID, "title": "Promise to write unit tests", "priority": "high"})
    assert res["status"] == "success"
    cmt_id = res["commitment_id"]

    # 2. List commitments
    list_res = await list_tool.execute({"_user_id": DEFAULT_LOCAL_USER_ID})
    assert list_res["status"] == "success"
    assert list_res["count"] == 1
    assert list_res["commitments"][0]["id"] == cmt_id

    # 3. Complete commitment only after approval
    commitment_before = await manager.get_commitment(cmt_id)
    complete_call = ToolCall(name="complete_commitment", arguments={"commitment_id": cmt_id})
    with pytest.raises(ToolConfirmationRequired):
        await executor.execute(complete_call, user_id=DEFAULT_LOCAL_USER_ID)
    assert (await manager.get_commitment(cmt_id)).updated_at == commitment_before.updated_at
    comp_res = await executor.execute(complete_call, is_pre_approved=True, user_id=DEFAULT_LOCAL_USER_ID)
    assert comp_res.success is True
    assert comp_res.output["status_value"] == "completed"
    assert "client_action" in comp_res.output

    # 4. Cancel commitment tool through ToolExecutor
    cmt2 = await manager.create_commitment(title="Commitment to cancel")
    call = ToolCall(name="cancel_commitment", arguments={"commitment_id": cmt2.id})

    # Must raise confirmation required when not pre-approved
    with pytest.raises(ToolConfirmationRequired) as exc_info:
        await executor.execute(call, is_pre_approved=False, user_id=DEFAULT_LOCAL_USER_ID)
    assert exc_info.value.pending.risk_level == ToolRiskLevel.MEDIUM
    assert cmt2.id in exc_info.value.pending.description

    # Approved execution succeeds
    exec_res = await executor.execute(call, is_pre_approved=True, user_id=DEFAULT_LOCAL_USER_ID)
    assert exec_res.success is True
    assert exec_res.output["status_value"] == "cancelled"
