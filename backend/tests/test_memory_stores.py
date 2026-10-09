import pytest
import pytest_asyncio
import os
import tempfile
from datetime import datetime, timezone, timedelta

from app.services.memory.schemas import (
    MemoryItem,
    MemoryType,
    MemorySource,
    MemoryStatus,
)
from app.services.memory.store import InMemoryMemoryStore, SQLiteMemoryStore


@pytest_asyncio.fixture(params=["in_memory", "sqlite"])
async def memory_store(request):
    """
    Parametrized fixture providing both store implementations
    to guarantee 100% behavioral parity.
    """
    if request.param == "in_memory":
        store = InMemoryMemoryStore()
        await store.initialize()
        yield store
        await store.close()
    else:
        # Create isolated temporary SQLite DB for each test
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_aurora.db")
        store = SQLiteMemoryStore(db_path=db_path)
        await store.initialize()
        yield store
        await store.close()
        if os.path.exists(db_path):
            os.remove(db_path)
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)


@pytest.mark.asyncio
async def test_store_put_and_get(memory_store):
    item = MemoryItem(
        type=MemoryType.PREFERENCE,
        content="Prefers standing desks and ergonomic keyboards.",
        importance=4,
        tags=["ergonomics", "hardware"]
    )

    saved = await memory_store.put(item)
    assert saved.id == item.id
    assert saved.content == item.content
    assert saved.tags == ["ergonomics", "hardware"]

    retrieved = await memory_store.get(item.id)
    assert retrieved is not None
    assert retrieved.id == item.id
    assert retrieved.content == item.content
    assert retrieved.importance == 4
    assert retrieved.tags == ["ergonomics", "hardware"]


@pytest.mark.asyncio
async def test_store_update_existing_item(memory_store):
    item = MemoryItem(
        id="mem_fixed_id",
        type=MemoryType.FACT,
        content="Current city is Seattle."
    )
    await memory_store.put(item)

    # Update content and importance
    item.content = "Current city is New York."
    item.importance = 5
    await memory_store.put(item)

    updated = await memory_store.get("mem_fixed_id")
    assert updated is not None
    assert updated.content == "Current city is New York."
    assert updated.importance == 5


@pytest.mark.asyncio
async def test_store_soft_and_hard_delete(memory_store):
    item1 = MemoryItem(type=MemoryType.FACT, content="Fact to soft delete.")
    item2 = MemoryItem(type=MemoryType.FACT, content="Fact to hard delete.")

    await memory_store.put(item1)
    await memory_store.put(item2)

    # Soft delete item1
    soft_deleted = await memory_store.delete(item1.id, soft=True)
    assert soft_deleted is True

    # Item1 still exists in get(), but is_active is False and status is ARCHIVED
    res1 = await memory_store.get(item1.id)
    assert res1 is not None
    assert res1.is_active is False
    assert res1.status == MemoryStatus.ARCHIVED

    # Item1 is excluded from active list by default
    active_list = await memory_store.list_memories(include_inactive=False)
    ids = [m.id for m in active_list]
    assert item1.id not in ids
    assert item2.id in ids

    # Item1 appears when include_inactive=True
    all_list = await memory_store.list_memories(include_inactive=True)
    assert item1.id in [m.id for m in all_list]

    # Hard delete item2
    hard_deleted = await memory_store.delete(item2.id, soft=False)
    assert hard_deleted is True

    # Item2 is completely gone
    assert await memory_store.get(item2.id) is None


@pytest.mark.asyncio
async def test_store_filtering_by_type_and_status(memory_store):
    m_pref = MemoryItem(type=MemoryType.PREFERENCE, content="Likes tea.")
    m_fact = MemoryItem(type=MemoryType.FACT, content="Speaks French.")
    m_task = MemoryItem(type=MemoryType.TASK, content="Buy milk.", status=MemoryStatus.PENDING_REVIEW)

    await memory_store.put(m_pref)
    await memory_store.put(m_fact)
    await memory_store.put(m_task)

    # Filter by type
    facts = await memory_store.list_memories(types=[MemoryType.FACT])
    assert len(facts) == 1
    assert facts[0].content == "Speaks French."

    # Filter by status
    pending = await memory_store.list_memories(statuses=[MemoryStatus.PENDING_REVIEW])
    assert len(pending) == 1
    assert pending[0].content == "Buy milk."


@pytest.mark.asyncio
async def test_store_filtering_by_source_and_tags(memory_store):
    m1 = MemoryItem(
        type=MemoryType.FACT,
        content="Cat name is Luna.",
        source=MemorySource.USER_EXPLICIT,
        tags=["pets", "cats"]
    )
    m2 = MemoryItem(
        type=MemoryType.FACT,
        content="User might enjoy hiking.",
        source=MemorySource.INFERRED,
        tags=["outdoors"]
    )

    await memory_store.put(m1)
    await memory_store.put(m2)

    explicit = await memory_store.list_memories(source=MemorySource.USER_EXPLICIT)
    assert len(explicit) == 1
    assert explicit[0].content == "Cat name is Luna."

    pet_memories = await memory_store.list_memories(tags=["pets"])
    assert len(pet_memories) == 1
    assert pet_memories[0].content == "Cat name is Luna."


@pytest.mark.asyncio
async def test_store_expiration_exclusion(memory_store):
    now = datetime.now(timezone.utc)

    active_mem = MemoryItem(
        type=MemoryType.TASK,
        content="Active ongoing project",
        expires_at=now + timedelta(days=5)
    )
    expired_mem = MemoryItem(
        type=MemoryType.TASK,
        content="Old promo code",
        expires_at=now - timedelta(hours=2)
    )

    await memory_store.put(active_mem)
    await memory_store.put(expired_mem)

    # Default excludes expired
    default_list = await memory_store.list_memories(include_expired=False)
    assert len(default_list) == 1
    assert default_list[0].id == active_mem.id

    # Including expired returns both
    with_expired = await memory_store.list_memories(include_expired=True)
    assert len(with_expired) == 2


@pytest.mark.asyncio
async def test_store_pagination(memory_store):
    for i in range(10):
        item = MemoryItem(
            type=MemoryType.FACT,
            content=f"Fact number {i:02d}",
            created_at=datetime.now(timezone.utc) + timedelta(seconds=i)
        )
        await memory_store.put(item)

    page1 = await memory_store.list_memories(limit=4, offset=0)
    page2 = await memory_store.list_memories(limit=4, offset=4)
    page3 = await memory_store.list_memories(limit=4, offset=8)

    assert len(page1) == 4
    assert len(page2) == 4
    assert len(page3) == 2

    # Verify order is created_at descending (most recent first)
    assert page1[0].content == "Fact number 09"
    assert page1[3].content == "Fact number 06"
    assert page2[0].content == "Fact number 05"


@pytest.mark.asyncio
async def test_store_lexical_search(memory_store):
    m1 = MemoryItem(
        type=MemoryType.PREFERENCE,
        content="I prefer dark chocolate over milk chocolate.",
        importance=5,
        tags=["food", "sweets"]
    )
    m2 = MemoryItem(
        type=MemoryType.FACT,
        content="My favorite dark beer is stout.",
        importance=2,
        tags=["drinks"]
    )
    m3 = MemoryItem(
        type=MemoryType.COMMITMENT,
        content="Review team PRs on GitHub by 5pm.",
        importance=4,
        tags=["work"]
    )

    await memory_store.put(m1)
    await memory_store.put(m2)
    await memory_store.put(m3)

    # Search for chocolate - exact match + higher importance should rank m1 first
    results = await memory_store.search_lexical("dark chocolate")
    assert len(results) >= 1
    assert results[0].id == m1.id

    # Search by tag
    tag_results = await memory_store.search_lexical("drinks")
    assert len(tag_results) >= 1
    assert tag_results[0].id == m2.id

    # Non-matching query returns empty
    empty_results = await memory_store.search_lexical("astrophysics")
    assert len(empty_results) == 0


@pytest.mark.asyncio
async def test_store_count(memory_store):
    assert await memory_store.count() == 0

    m1 = MemoryItem(type=MemoryType.FACT, content="First fact")
    m2 = MemoryItem(type=MemoryType.FACT, content="Second fact")
    await memory_store.put(m1)
    await memory_store.put(m2)

    assert await memory_store.count() == 2

    # Soft delete m1
    await memory_store.delete(m1.id, soft=True)
    assert await memory_store.count(include_inactive=False) == 1
    assert await memory_store.count(include_inactive=True) == 2
