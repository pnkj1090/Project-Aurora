import pytest
from datetime import datetime, timezone, timedelta

from app.services.memory.schemas import (
    MemoryItem,
    MemoryType,
    MemorySource,
    MemoryStatus,
)
from app.services.memory.store import InMemoryMemoryStore
from app.services.memory.retriever import LexicalMemoryRetriever


@pytest.fixture
def store():
    return InMemoryMemoryStore()


@pytest.fixture
def retriever(store):
    return LexicalMemoryRetriever(store=store)


@pytest.mark.asyncio
async def test_lexical_token_matching(store, retriever):
    await store.put(MemoryItem(
        content="I love Earl Grey tea with lemon in the afternoon.",
        type=MemoryType.FACT,
        importance=3
    ))
    await store.put(MemoryItem(
        content="The server deployment failed on port 8080.",
        type=MemoryType.EPISODIC,
        importance=3
    ))

    results = await retriever.retrieve(query="earl grey tea")
    assert len(results) == 1
    assert "Earl Grey" in results[0].content


@pytest.mark.asyncio
async def test_exact_phrase_priority(store, retriever):
    # Both items match tokens, but one has the exact phrase match
    item1 = await store.put(MemoryItem(
        content="My favorite IDE is Visual Studio Code for Python development.",
        type=MemoryType.FACT,
        importance=3
    ))
    item2 = await store.put(MemoryItem(
        content="I do Python development in Visual Studio.",
        type=MemoryType.FACT,
        importance=3
    ))

    # Searching exact phrase "Visual Studio Code"
    results = await retriever.retrieve(query="Visual Studio Code")
    assert len(results) >= 1
    # First result must be item1 because of the +10 exact phrase match
    assert results[0].id == item1.id


@pytest.mark.asyncio
async def test_tag_matching_boost(store, retriever):
    # Both items have similar base content, but item_with_tag has a matching tag
    item_no_tag = await store.put(MemoryItem(
        content="Weekly project check-in notes.",
        type=MemoryType.TASK,
        importance=3,
        tags=[]
    ))
    item_with_tag = await store.put(MemoryItem(
        content="Weekly project check-in notes.",
        type=MemoryType.TASK,
        importance=3,
        tags=["urgent", "team"]
    ))

    # Query 'project urgent' matches 'project' in content for both, plus 'urgent' tag in item_with_tag
    results = await retriever.retrieve(query="project urgent")
    assert len(results) >= 2
    assert results[0].id == item_with_tag.id



@pytest.mark.asyncio
async def test_type_filtering(store, retriever):
    await store.put(MemoryItem(
        content="I prefer dark mode in all apps.",
        type=MemoryType.PREFERENCE,
        importance=4
    ))
    await store.put(MemoryItem(
        content="Remember to fix dark mode contrast in task 102.",
        type=MemoryType.TASK,
        importance=4
    ))

    # Retrieve with type filter
    pref_results = await retriever.retrieve(
        query="dark mode",
        types=[MemoryType.PREFERENCE]
    )
    assert len(pref_results) == 1
    assert pref_results[0].type == MemoryType.PREFERENCE

    task_results = await retriever.retrieve(
        query="dark mode",
        types=[MemoryType.TASK]
    )
    assert len(task_results) == 1
    assert task_results[0].type == MemoryType.TASK


@pytest.mark.asyncio
async def test_importance_weighting(store, retriever):
    low_imp = await store.put(MemoryItem(
        content="Meeting with team tomorrow.",
        type=MemoryType.FACT,
        importance=1
    ))
    high_imp = await store.put(MemoryItem(
        content="Meeting with executive team tomorrow.",
        type=MemoryType.FACT,
        importance=5
    ))

    results = await retriever.retrieve(query="meeting tomorrow")
    assert len(results) == 2
    # Higher importance should rank first
    assert results[0].id == high_imp.id
    assert results[1].id == low_imp.id


@pytest.mark.asyncio
async def test_recency_decay(store, retriever):
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=60)

    old_item = await store.put(MemoryItem(
        content="Key password reset request submitted.",
        type=MemoryType.FACT,
        importance=3,
        created_at=old_time
    ))
    recent_item = await store.put(MemoryItem(
        content="Key password reset request submitted.",
        type=MemoryType.FACT,
        importance=3,
        created_at=now
    ))

    results = await retriever.retrieve(query="password reset request")
    assert len(results) == 2
    # More recent item must rank ahead of the 60-day old item
    assert results[0].id == recent_item.id
    assert results[1].id == old_item.id


@pytest.mark.asyncio
async def test_expired_and_archived_memory_exclusion(store, retriever):
    now = datetime.now(timezone.utc)

    # Active and valid
    valid = await store.put(MemoryItem(
        content="Project deadline is Friday.",
        type=MemoryType.TASK,
        status=MemoryStatus.ACTIVE,
        is_active=True
    ))

    # Expired item
    await store.put(MemoryItem(
        content="Project deadline was yesterday.",
        type=MemoryType.TASK,
        expires_at=now - timedelta(days=1),
        status=MemoryStatus.ACTIVE,
        is_active=True
    ))

    # Archived / inactive item
    await store.put(MemoryItem(
        content="Project deadline draft was canceled.",
        type=MemoryType.TASK,
        status=MemoryStatus.ARCHIVED,
        is_active=False
    ))

    results = await retriever.retrieve(query="project deadline")
    assert len(results) == 1
    assert results[0].id == valid.id


@pytest.mark.asyncio
async def test_result_limits(store, retriever):
    for i in range(10):
        await store.put(MemoryItem(
            content=f"Important project note number {i}",
            type=MemoryType.FACT,
            importance=3
        ))

    results = await retriever.retrieve(query="important project note", limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_empty_query_returns_empty(retriever):
    results = await retriever.retrieve(query="   ")
    assert results == []
