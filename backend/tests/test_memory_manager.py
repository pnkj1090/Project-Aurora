import pytest
from datetime import datetime, timezone, timedelta

from app.services.memory.schemas import (
    MemoryItem,
    MemoryType,
    MemorySource,
    MemoryStatus,
)
from app.services.memory.store import InMemoryMemoryStore
from app.services.memory.manager import MemoryManager
from app.services.memory.retriever import LexicalMemoryRetriever


@pytest.fixture
def memory_manager():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    return MemoryManager(store=store, retriever=retriever)


@pytest.mark.asyncio
async def test_create_explicit_memory(memory_manager):
    item, is_created = await memory_manager.create_memory(
        content="I prefer dark mode in all user interfaces.",
        type=MemoryType.PREFERENCE,
        importance=4,
        tags=["ui", "theme"]
    )

    assert is_created is True
    assert item.id is not None
    assert item.content == "I prefer dark mode in all user interfaces."
    assert item.type == MemoryType.PREFERENCE
    assert item.source == MemorySource.USER_EXPLICIT
    assert item.status == MemoryStatus.ACTIVE
    assert item.confidence == 1.0
    assert item.importance == 4
    assert item.tags == ["theme", "ui"]
    assert item.is_active is True


@pytest.mark.asyncio
async def test_validation_and_normalization(memory_manager):
    # Empty content raises ValueError
    with pytest.raises(ValueError, match="cannot be empty"):
        await memory_manager.create_memory(content="   \n\t  ")

    # Clamping importance and confidence
    item, _ = await memory_manager.create_memory(
        content="   Excess   whitespace   and   tabs   ",
        importance=99,
        confidence=2.5,
        tags=["#Work", " WORK ", "#apple", "apple"]
    )

    assert item.content == "Excess whitespace and tabs"
    assert item.importance == 5  # Clamped to max 5
    assert item.confidence == 1.0  # Clamped to max 1.0
    assert item.tags == ["apple", "work"]  # Deduplicated, lowercase, stripped #


@pytest.mark.asyncio
async def test_exact_deduplication(memory_manager):
    # Create first memory
    item1, is_created1 = await memory_manager.create_memory(
        content="My favorite coffee is cappuccino.",
        type=MemoryType.FACT,
        importance=3,
        tags=["coffee"]
    )
    assert is_created1 is True

    # Re-storing identical content with different importance & extra tags
    item2, is_created2 = await memory_manager.create_memory(
        content="  my favorite coffee is cappuccino.  ",
        type=MemoryType.FACT,
        importance=5,
        tags=["beverage", "coffee"]
    )
    assert is_created2 is False
    assert item2.id == item1.id
    assert item2.importance == 5
    assert sorted(item2.tags) == ["beverage", "coffee"]

    # Verify store has only 1 active memory
    all_memories = await memory_manager.list_memories()
    assert len(all_memories) == 1


@pytest.mark.asyncio
async def test_preference_conflict_handling(memory_manager):
    # User originally prefers dark mode
    item1, is_created1 = await memory_manager.create_memory(
        content="I prefer dark mode.",
        type=MemoryType.PREFERENCE,
        importance=4,
        tags=["theme", "ui"]
    )
    assert is_created1 is True

    # User later says they prefer light mode for reading, sharing the "theme" tag
    item2, is_created2 = await memory_manager.create_memory(
        content="I prefer light mode for all screens now.",
        type=MemoryType.PREFERENCE,
        importance=5,
        tags=["theme", "display"]
    )
    # Detected tag overlap on PREFERENCE -> updates existing preference rather than keeping conflicting directions
    assert is_created2 is False
    assert item2.id == item1.id
    assert item2.content == "I prefer light mode for all screens now."
    assert item2.importance == 5
    assert set(item2.tags) == {"theme", "ui", "display"}

    active_prefs = await memory_manager.list_memories(types=[MemoryType.PREFERENCE])
    assert len(active_prefs) == 1


@pytest.mark.asyncio
async def test_update_memory(memory_manager):
    item, _ = await memory_manager.create_memory(
        content="Doctor appointment next Tuesday.",
        type=MemoryType.TASK,
        importance=3
    )

    updated = await memory_manager.update_memory(
        memory_id=item.id,
        content="Doctor appointment rescheduled to Wednesday.",
        importance=4,
        tags=["health", "appointment"],
        status=MemoryStatus.ACTIVE
    )

    assert updated is not None
    assert updated.content == "Doctor appointment rescheduled to Wednesday."
    assert updated.importance == 4
    assert updated.tags == ["appointment", "health"]

    # Updating non-existent ID
    none_res = await memory_manager.update_memory(memory_id="unknown-id", content="test")
    assert none_res is None


@pytest.mark.asyncio
async def test_expiration_and_cleanup(memory_manager):
    now = datetime.now(timezone.utc)
    # Expired memory
    expired_item, _ = await memory_manager.create_memory(
        content="Temporary discount code expires in 1 minute.",
        type=MemoryType.FACT,
        expires_at=now - timedelta(minutes=5)
    )

    # Valid unexpired memory
    valid_item, _ = await memory_manager.create_memory(
        content="Long term project goal.",
        type=MemoryType.TASK,
        expires_at=now + timedelta(days=10)
    )

    # list_memories by default excludes expired
    active_unexpired = await memory_manager.list_memories(include_expired=False)
    assert len(active_unexpired) == 1
    assert active_unexpired[0].id == valid_item.id

    # Running cleanup
    cleaned = await memory_manager.cleanup_expired()
    assert cleaned == 1

    # Check that expired item is now archived / inactive
    check_item = await memory_manager.get_memory(expired_item.id)
    assert check_item.is_active is False
    assert check_item.status == MemoryStatus.ARCHIVED


@pytest.mark.asyncio
async def test_soft_and_hard_delete(memory_manager):
    item, _ = await memory_manager.create_memory(
        content="Forgettable note.",
        type=MemoryType.EPISODIC
    )

    # Soft delete archives the memory
    soft_res = await memory_manager.delete_memory(item.id, soft=True)
    assert soft_res is True

    archived = await memory_manager.get_memory(item.id)
    assert archived is not None
    assert archived.is_active is False
    assert archived.status == MemoryStatus.ARCHIVED

    # Hard delete purges it completely
    hard_res = await memory_manager.delete_memory(item.id, soft=False)
    assert hard_res is True

    purged = await memory_manager.get_memory(item.id)
    assert purged is None
