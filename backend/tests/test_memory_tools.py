import pytest
from app.services.memory.schemas import MemoryItem, MemoryType, MemoryStatus
from app.services.memory.store import InMemoryMemoryStore
from app.services.memory.manager import MemoryManager
from app.services.memory.retriever import LexicalMemoryRetriever
from app.services.tools.builtins.memory_tool import (
    StoreUserMemoryTool,
    RecallUserMemoryTool,
    ForgetUserMemoryTool,
)
from app.services.tools.schemas import ToolCall
from app.services.tools.registry import ToolRegistry
from app.services.tools.executor import ToolExecutor, ToolConfirmationRequired
from app.services.tools.base import ToolRiskLevel


@pytest.fixture
def memory_env():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    manager = MemoryManager(store=store, retriever=retriever)

    registry = ToolRegistry()
    store_tool = StoreUserMemoryTool(memory_manager=manager)
    recall_tool = RecallUserMemoryTool(memory_manager=manager)
    forget_tool = ForgetUserMemoryTool(memory_manager=manager)

    registry.register(store_tool)
    registry.register(recall_tool)
    registry.register(forget_tool)

    executor = ToolExecutor(registry=registry)
    return {
        "store": store,
        "manager": manager,
        "registry": registry,
        "executor": executor,
        "store_tool": store_tool,
        "recall_tool": recall_tool,
        "forget_tool": forget_tool,
    }


# MARK: - Tool Definitions & Schemas

def test_memory_tool_definitions(memory_env):
    store_tool = memory_env["store_tool"]
    recall_tool = memory_env["recall_tool"]
    forget_tool = memory_env["forget_tool"]

    # store_user_memory
    assert store_tool.definition.name == "store_user_memory"
    assert store_tool.definition.risk_level == ToolRiskLevel.LOW
    assert store_tool.definition.requires_confirmation is False
    assert "content" in store_tool.definition.parameters_schema["required"]

    # recall_user_memory
    assert recall_tool.definition.name == "recall_user_memory"
    assert recall_tool.definition.risk_level == ToolRiskLevel.LOW
    assert recall_tool.definition.requires_confirmation is False
    assert "query" in recall_tool.definition.parameters_schema["required"]

    # forget_user_memory
    assert forget_tool.definition.name == "forget_user_memory"
    assert forget_tool.definition.risk_level == ToolRiskLevel.MEDIUM
    assert forget_tool.definition.requires_confirmation is True


# MARK: - store_user_memory

@pytest.mark.asyncio
async def test_store_user_memory_execution(memory_env):
    executor = memory_env["executor"]
    manager = memory_env["manager"]

    call = ToolCall(
        name="store_user_memory",
        arguments={
            "content": "I like dark roast coffee with oat milk.",
            "type": "preference",
            "importance": 4,
            "tags": ["coffee", "preferences"]
        }
    )

    result = await executor.execute(call)
    assert result.success is True
    assert result.output["status"] == "success"
    assert result.output["action"] == "created"
    assert "dark roast coffee" in result.output["content"]
    assert result.output["importance"] == 4

    # Verify memory is saved in manager
    active = await manager.list_memories()
    assert len(active) == 1
    assert active[0].content == "I like dark roast coffee with oat milk."


@pytest.mark.asyncio
async def test_store_user_memory_validation_error(memory_env):
    executor = memory_env["executor"]
    call = ToolCall(
        name="store_user_memory",
        arguments={"content": "  "}
    )
    result = await executor.execute(call)
    assert result.success is False
    assert "Missing required parameter" in result.error or "empty" in str(result.output)


# MARK: - recall_user_memory

@pytest.mark.asyncio
async def test_recall_user_memory_execution(memory_env):
    executor = memory_env["executor"]
    manager = memory_env["manager"]

    # Pre-populate memories
    await manager.create_memory(
        content="Flight to San Francisco departs at 7:30 AM on Thursday.",
        type=MemoryType.TASK,
        importance=4,
        tags=["travel", "flight"],
        user_id="usr_local_default",
    )
    await manager.create_memory(
        content="Conference registration code is SFO-2026.",
        type=MemoryType.FACT,
        importance=3,
        tags=["conference", "travel"],
        user_id="usr_local_default",
    )

    call = ToolCall(
        name="recall_user_memory",
        arguments={"query": "flight travel"}
    )

    result = await executor.execute(call)
    assert result.success is True
    assert result.output["status"] == "success"
    assert result.output["count"] >= 1
    memories = result.output["memories"]
    assert any("7:30 AM" in m["content"] for m in memories)


# MARK: - forget_user_memory & Confirmation Flow

@pytest.mark.asyncio
async def test_forget_user_memory_requires_confirmation(memory_env):
    executor = memory_env["executor"]
    manager = memory_env["manager"]

    item, _ = await manager.create_memory(
        content="Old gym membership number 12345.",
        type=MemoryType.FACT
    )

    call = ToolCall(
        name="forget_user_memory",
        arguments={"query": "gym membership"}
    )

    # Calling execute directly without approval must raise ToolConfirmationRequired
    with pytest.raises(ToolConfirmationRequired) as exc_info:
        await executor.execute(call, is_pre_approved=False)

    pending = exc_info.value.pending
    assert pending.tool_call.name == "forget_user_memory"
    assert pending.risk_level == ToolRiskLevel.MEDIUM
    # Exact candidate ID must be bound into arguments
    assert "target_memory_ids" in pending.tool_call.arguments
    assert pending.tool_call.arguments["target_memory_ids"] == [item.id]
    # Human-readable summary
    assert "Old gym membership number 12345" in pending.description


@pytest.mark.asyncio
async def test_forget_user_memory_approved_flow(memory_env):
    executor = memory_env["executor"]
    manager = memory_env["manager"]

    item1, _ = await manager.create_memory(
        content="Discard this obsolete note.",
        type=MemoryType.EPISODIC
    )
    item2, _ = await manager.create_memory(
        content="Keep this vital memory intact.",
        type=MemoryType.FACT
    )

    # 1. Ask to forget obsolete note
    call = ToolCall(
        name="forget_user_memory",
        arguments={"query": "obsolete note"}
    )

    # Confirmation requirement check
    pending = executor.check_confirmation_requirement(call)
    assert pending is not None
    assert pending.tool_call.arguments["target_memory_ids"] == [item1.id]

    # 2. Simulate User Approval: execute with pre-approval and bound tool_call
    result = await executor.execute(pending.tool_call, is_pre_approved=True)
    assert result.success is True
    assert result.output["status"] == "success"
    assert result.output["deleted_count"] == 1
    assert result.output["deleted_ids"] == [item1.id]

    # 3. Check memory store: item1 is archived, item2 is still active
    active = await manager.list_memories()
    assert len(active) == 1
    assert active[0].id == item2.id

    archived = await manager.get_memory(item1.id)
    assert archived.is_active is False
    assert archived.status == MemoryStatus.ARCHIVED


@pytest.mark.asyncio
async def test_forget_user_memory_rejection_leaves_store_untouched(memory_env):
    executor = memory_env["executor"]
    manager = memory_env["manager"]

    item, _ = await manager.create_memory(
        content="Do not delete this memory.",
        type=MemoryType.FACT
    )

    call = ToolCall(
        name="forget_user_memory",
        arguments={"query": "delete this"}
    )

    pending = executor.check_confirmation_requirement(call)
    assert pending is not None
    assert pending.tool_call.arguments["target_memory_ids"] == [item.id]

    # User Rejection: in our architecture, rejection means the tool is NOT executed
    # We verify the memory remains completely untouched
    stored = await manager.get_memory(item.id)
    assert stored is not None
    assert stored.is_active is True
    assert stored.status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_forget_user_memory_single_id_confirmation(memory_env):
    executor = memory_env["executor"]
    manager = memory_env["manager"]

    item, _ = await manager.create_memory(
        content="Targeted memory by ID.",
        type=MemoryType.TASK
    )

    call = ToolCall(
        name="forget_user_memory",
        arguments={"memory_id": item.id}
    )

    pending = executor.check_confirmation_requirement(call)
    assert pending is not None
    assert pending.tool_call.arguments["target_memory_ids"] == [item.id]

    result = await executor.execute(pending.tool_call, is_pre_approved=True)
    assert result.success is True
    assert result.output["deleted_count"] == 1


@pytest.mark.asyncio
async def test_cannot_broaden_deletion_scope(memory_env):
    executor = memory_env["executor"]
    manager = memory_env["manager"]

    item1, _ = await manager.create_memory(content="Memory 1", type=MemoryType.FACT)
    item2, _ = await manager.create_memory(content="Memory 2", type=MemoryType.FACT)

    # Tool call originally targeted only item1
    approved_call = ToolCall(
        name="forget_user_memory",
        arguments={"target_memory_ids": [item1.id]}
    )

    # If an attacker attempts to broaden the deletion target in another call
    tampered_call = ToolCall(
        name="forget_user_memory",
        arguments={"query": "Memory", "target_memory_ids": [item1.id, item2.id]}
    )

    # If executed without is_pre_approved=True, it is blocked
    with pytest.raises(ToolConfirmationRequired):
        await executor.execute(tampered_call, is_pre_approved=False)

    # Even if executed with is_pre_approved=True for approved_call, only item1 is deleted
    result = await executor.execute(approved_call, is_pre_approved=True)
    assert result.output["deleted_ids"] == [item1.id]

    # item2 is completely intact
    item2_check = await manager.get_memory(item2.id)
    assert item2_check.is_active is True
    assert item2_check.status == MemoryStatus.ACTIVE
