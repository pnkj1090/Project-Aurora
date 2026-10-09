import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.memory.schemas import (
    MemoryItem,
    MemoryType,
    MemorySource,
    MemoryStatus,
    MemoryLifecycleState,
    evaluate_lifecycle_state,
)
from app.services.memory.retriever import (
    LexicalMemoryRetriever,
    calculate_freshness,
)
from app.services.memory.manager import (
    MemoryManager,
    DuplicateClassification,
)
from app.services.memory import get_memory_manager_dependency
from app.services.memory.store import InMemoryMemoryStore
from app.services.context.providers.memory import MemoryContextProvider
from app.services.conversation.session import ConversationSession


# ══════════════════════════════════════════════════════════════════════════════
# Group A: Memory Lifecycle
# ══════════════════════════════════════════════════════════════════════════════

def test_lifecycle_active():
    item = MemoryItem(
        content="Active item",
        type=MemoryType.FACT,
        status=MemoryStatus.ACTIVE,
        is_active=True,
        expires_at=None,
    )
    assert evaluate_lifecycle_state(item) == MemoryLifecycleState.ACTIVE


def test_lifecycle_expired():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    expired_time = now - timedelta(hours=1)
    item = MemoryItem(
        content="Expired item",
        type=MemoryType.TASK,
        status=MemoryStatus.ACTIVE,
        is_active=True,
        expires_at=expired_time,
    )
    assert item.is_expired_at(now) is True
    assert evaluate_lifecycle_state(item, now=now) == MemoryLifecycleState.EXPIRED


def test_lifecycle_archived():
    item = MemoryItem(
        content="Archived item",
        type=MemoryType.FACT,
        status=MemoryStatus.ARCHIVED,
        is_active=True,
    )
    assert evaluate_lifecycle_state(item) == MemoryLifecycleState.ARCHIVED


def test_lifecycle_soft_deleted():
    item = MemoryItem(
        content="Soft deleted item",
        type=MemoryType.FACT,
        status=MemoryStatus.ACTIVE,
        is_active=False,
    )
    assert evaluate_lifecycle_state(item) == MemoryLifecycleState.SOFT_DELETED


def test_lifecycle_permanent():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    item = MemoryItem(
        content="Permanent note",
        type=MemoryType.PREFERENCE,
        status=MemoryStatus.ACTIVE,
        is_active=True,
        expires_at=None,
    )
    assert item.is_expired_at(now) is False
    assert evaluate_lifecycle_state(item, now=now) == MemoryLifecycleState.ACTIVE


def test_lifecycle_precedence():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    past = now - timedelta(days=1)

    # SOFT_DELETED > ARCHIVED > EXPIRED > ACTIVE
    # Case 1: is_active=False wins over ARCHIVED and EXPIRED
    item1 = MemoryItem(
        content="Item 1",
        type=MemoryType.FACT,
        status=MemoryStatus.ARCHIVED,
        is_active=False,
        expires_at=past,
    )
    assert evaluate_lifecycle_state(item1, now=now) == MemoryLifecycleState.SOFT_DELETED

    # Case 2: ARCHIVED wins over EXPIRED when is_active=True
    item2 = MemoryItem(
        content="Item 2",
        type=MemoryType.FACT,
        status=MemoryStatus.ARCHIVED,
        is_active=True,
        expires_at=past,
    )
    assert evaluate_lifecycle_state(item2, now=now) == MemoryLifecycleState.ARCHIVED

    # Case 3: EXPIRED wins over ACTIVE
    item3 = MemoryItem(
        content="Item 3",
        type=MemoryType.FACT,
        status=MemoryStatus.ACTIVE,
        is_active=True,
        expires_at=past,
    )
    assert evaluate_lifecycle_state(item3, now=now) == MemoryLifecycleState.EXPIRED

    # Case 4: ACTIVE when unexpired, active status, is_active=True
    item4 = MemoryItem(
        content="Item 4",
        type=MemoryType.FACT,
        status=MemoryStatus.ACTIVE,
        is_active=True,
        expires_at=now + timedelta(days=1),
    )
    assert evaluate_lifecycle_state(item4, now=now) == MemoryLifecycleState.ACTIVE


@pytest.mark.asyncio
async def test_expired_remains_persisted():
    store = InMemoryMemoryStore()
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    item = MemoryItem(
        content="Old expired note",
        type=MemoryType.FACT,
        expires_at=now - timedelta(days=5),
        status=MemoryStatus.ACTIVE,
        is_active=True,
    )
    await store.put(item)

    # Verify state evaluation is strictly read-only and record persists
    state = evaluate_lifecycle_state(item, now=now)
    assert state == MemoryLifecycleState.EXPIRED

    persisted = await store.get(item.id)
    assert persisted is not None
    assert persisted.id == item.id
    assert persisted.is_active is True
    assert persisted.status == MemoryStatus.ACTIVE


# ══════════════════════════════════════════════════════════════════════════════
# Group B: Freshness & Recency
# ══════════════════════════════════════════════════════════════════════════════

def test_freshness_permanent_decay_and_floor():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Day 0: age = 0 -> 1.0 / (1.0 + 0) = 1.0
    item0 = MemoryItem(content="M0", type=MemoryType.FACT, created_at=now, updated_at=now, expires_at=None)
    assert calculate_freshness(item0, now=now) == 1.0

    # Day 30: 1.0 / (1.0 + 0.02 * 30) = 1.0 / 1.6 = 0.625
    t30 = now - timedelta(days=30)
    item30 = MemoryItem(content="M30", type=MemoryType.FACT, created_at=t30, updated_at=t30, expires_at=None)
    f30 = calculate_freshness(item30, now=now)
    assert abs(f30 - 0.625) < 1e-4

    # Day 90: 1.0 / (1.0 + 0.02 * 90) = 1.0 / 2.8 ≈ 0.3571 -> clamped to floor 0.50
    t90 = now - timedelta(days=90)
    item90 = MemoryItem(content="M90", type=MemoryType.FACT, created_at=t90, updated_at=t90, expires_at=None)
    assert calculate_freshness(item90, now=now) == 0.50

    # Day 365: clamped to floor 0.50
    t365 = now - timedelta(days=365)
    item365 = MemoryItem(content="M365", type=MemoryType.FACT, created_at=t365, updated_at=t365, expires_at=None)
    assert calculate_freshness(item365, now=now) == 0.50


def test_freshness_expiring_decay_and_exact_boundary():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    t_start = now - timedelta(days=2)
    t_end = now + timedelta(days=8)
    # Lifespan = 10 days, remaining = 8 days
    # Freshness = 0.20 + 0.80 * (8 / 10) = 0.20 + 0.64 = 0.84
    item = MemoryItem(content="Expiring", type=MemoryType.FACT, created_at=t_start, updated_at=t_start, expires_at=t_end)
    f = calculate_freshness(item, now=now)
    assert abs(f - 0.84) < 1e-4

    # Exact expiration: now == expires_at -> remaining = 0 -> freshness = 0.20
    item_exact = MemoryItem(content="Exact", type=MemoryType.FACT, created_at=t_start, updated_at=t_start, expires_at=now)
    f_exact = calculate_freshness(item_exact, now=now)
    assert abs(f_exact - 0.20) < 1e-4

    # Past expiration: now > expires_at -> freshness = 0.0
    item_past = MemoryItem(content="Past", type=MemoryType.FACT, created_at=t_start, updated_at=t_start, expires_at=now - timedelta(seconds=1))
    assert calculate_freshness(item_past, now=now) == 0.0


def test_freshness_deterministic_and_no_mutation():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    item = MemoryItem(content="Test item", type=MemoryType.FACT, created_at=now - timedelta(days=10), expires_at=None)
    item_dict_before = item.model_dump()

    # Calculate multiple times with deterministic now
    f1 = calculate_freshness(item, now=now)
    f2 = calculate_freshness(item, now=now)
    assert f1 == f2
    assert item.model_dump() == item_dict_before



# ══════════════════════════════════════════════════════════════════════════════
# Group C: Lexical Signals, Semantic Modifiers, and Ranking
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_ranking_signals_and_lexical_primacy():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Exact phrase boost (+10.0) vs individual token matches
    exact_item = MemoryItem(
        id="mem_exact",
        content="Deep learning optimization algorithm",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        source=MemorySource.INFERRED,
        metadata={"user_id": "u1"},
        created_at=now,
        updated_at=now,
    )
    partial_item = MemoryItem(
        id="mem_partial",
        content="Optimization problem with learning parameters",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        source=MemorySource.INFERRED,
        metadata={"user_id": "u1"},
        created_at=now,
        updated_at=now,
    )
    await store.put(exact_item)
    await store.put(partial_item)

    results = await retriever.retrieve("deep learning optimization", user_id="u1", now=now)
    assert len(results) >= 2
    assert results[0].id == "mem_exact"


@pytest.mark.asyncio
async def test_lexical_primacy_zero_lexical_score_excluded():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Item has high importance, high confidence, user_explicit, preference (+1.5 bonus)
    # BUT disjoint lexical tokens
    item = MemoryItem(
        id="mem_disjoint",
        content="Completely unrelated topic about astronomy and stars",
        type=MemoryType.PREFERENCE,
        importance=5,
        confidence=1.0,
        source=MemorySource.USER_EXPLICIT,
        metadata={"user_id": "u1"},
        created_at=now,
        updated_at=now,
    )
    await store.put(item)

    # Query has 0 token overlap
    results = await retriever.retrieve("cooking recipe soup ingredients", user_id="u1", now=now)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_ranking_source_and_semantic_type_modifiers():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Both items have identical lexical match
    # Item A: USER_EXPLICIT (1.10)
    # Item B: INFERRED (1.00)
    item_user = MemoryItem(
        id="mem_user",
        content="Deploy workflow configuration",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        source=MemorySource.USER_EXPLICIT,
        metadata={"user_id": "u1"},
        created_at=now,
        updated_at=now,
    )
    item_inferred = MemoryItem(
        id="mem_inferred",
        content="Deploy workflow configuration",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        source=MemorySource.INFERRED,
        metadata={"user_id": "u1"},
        created_at=now,
        updated_at=now,
    )
    await store.put(item_user)
    await store.put(item_inferred)

    res = await retriever.retrieve("deploy workflow", user_id="u1", now=now)
    assert res[0].id == "mem_user"


@pytest.mark.asyncio
async def test_episodic_temporal_boost():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    episodic_item = MemoryItem(
        id="mem_ep",
        content="Discussion about project architecture roadmap",
        type=MemoryType.EPISODIC,
        importance=3,
        confidence=1.0,
        source=MemorySource.INFERRED,
        metadata={"user_id": "u1"},
        created_at=now,
        updated_at=now,
    )
    task_item = MemoryItem(
        id="mem_task",
        content="Discussion about project architecture roadmap",
        type=MemoryType.TASK,
        importance=3,
        confidence=1.0,
        source=MemorySource.INFERRED,
        metadata={"user_id": "u1"},
        created_at=now,
        updated_at=now,
    )
    await store.put(episodic_item)
    await store.put(task_item)

    # Query WITH temporal keyword "yesterday" but memory is NOT temporally anchored:
    # Remains 0.95 -> TASK (1.00) > EPISODIC (0.95)
    res_unanchored = await retriever.retrieve("project architecture roadmap yesterday", user_id="u1", now=now)
    assert res_unanchored[0].id == "mem_task"
    assert res_unanchored[1].id == "mem_ep"

    # Now make episodic item temporally anchored in metadata
    episodic_item.metadata["temporal_anchor"] = "yesterday"
    await store.put(episodic_item)

    # Temporally anchored EPISODIC boosted to 1.15 > TASK (1.00)
    res_anchored = await retriever.retrieve("project architecture roadmap yesterday", user_id="u1", now=now)
    assert res_anchored[0].id == "mem_ep"
    assert res_anchored[1].id == "mem_task"


# ══════════════════════════════════════════════════════════════════════════════
# Group D: Deterministic 4-Tuple Tie-Breaking
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_deterministic_tie_breaking_order_and_repeatability():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # 3 items with identical scores, but differing updated_at, created_at, id
    t1 = now - timedelta(hours=3)
    t2 = now - timedelta(hours=2)
    t3 = now - timedelta(hours=1)

    item_a = MemoryItem(
        id="mem_aaa",
        content="System configuration note",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        created_at=t1,
        updated_at=t2,
        metadata={"user_id": "u1"},
    )
    item_b = MemoryItem(
        id="mem_bbb",
        content="System configuration note",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        created_at=t1,
        updated_at=t3,  # More recently updated -> should be first
        metadata={"user_id": "u1"},
    )
    item_c = MemoryItem(
        id="mem_ccc",
        content="System configuration note",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        created_at=t1,
        updated_at=t2,
        metadata={"user_id": "u1"},
    )
    store._memories[item_a.id] = item_a
    store._memories[item_b.id] = item_b
    store._memories[item_c.id] = item_c

    # Repeated 100 times: must produce identical ordering every time
    first_order = None
    for _ in range(100):
        results = await retriever.retrieve("system configuration", user_id="u1", now=now)
        order = [r.id for r in results]
        if first_order is None:
            first_order = order
            # mem_bbb (t3) must be first; between aaa and ccc (both t2), mem_aaa < mem_ccc alphabetically
            assert order == ["mem_bbb", "mem_aaa", "mem_ccc"]
        else:
            assert order == first_order



# ══════════════════════════════════════════════════════════════════════════════
# Group E: Limit Contract & Context Budgets
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_retriever_limit_contract():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Negative limit raises ValueError
    with pytest.raises(ValueError):
        await retriever.retrieve("query", user_id="u1", limit=-1, now=now)

    # 2. Limit == 0 returns []
    res_zero = await retriever.retrieve("query", user_id="u1", limit=0, now=now)
    assert res_zero == []

    # 3. Create 25 matching items
    for i in range(25):
        await store.put(
            MemoryItem(
                id=f"mem_{i:02d}",
                content=f"Important meeting note {i}",
                type=MemoryType.FACT,
                metadata={"user_id": "u1"},
                created_at=now,
                updated_at=now,
            )
        )

    # Default limit is 5
    res_default = await retriever.retrieve("meeting note", user_id="u1", now=now)
    assert len(res_default) == 5

    # Limit > 20 is clamped to 20
    res_clamped = await retriever.retrieve("meeting note", user_id="u1", limit=50, now=now)
    assert len(res_clamped) == 20


@pytest.mark.asyncio
async def test_context_budgets_and_truncation():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)
    provider = MemoryContextProvider(memory_manager=manager, max_memories=5)

    # 1. Per-memory truncation: 600 characters should be truncated to 500 characters
    long_content = "A" * 600
    await manager.create_memory_for_user(
        user_id="u_budget",
        content=long_content,
        type=MemoryType.FACT,
    )

    session = ConversationSession(conversation_id="s1", metadata={"user_id": "u_budget"})
    block = await provider.get_context(session=session, query="A")
    assert block is not None
    # Ensure memory block exists and the 600 chars were truncated with ...
    assert len(long_content) > 500
    assert "..." in block.content
    assert "A" * 501 not in block.content

    # 2. Total context budget: 2,000 characters
    # Add multiple items that would exceed 2,000 chars total
    for i in range(10):
        await manager.create_memory_for_user(
            user_id="u_budget_2",
            content=f"Entry {i:02d}: " + "B" * 400,
            type=MemoryType.FACT,
        )
    session2 = ConversationSession(conversation_id="s2", metadata={"user_id": "u_budget_2"})
    block2 = await provider.get_context(session=session2, query="Entry B")
    assert block2 is not None
    assert len(block2.content) <= 2000


# ══════════════════════════════════════════════════════════════════════════════
# Group G: Duplicate Classification (Read-Only)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_duplicate_classification_exact_possible_none():
    manager = MemoryManager(store=InMemoryMemoryStore())

    # Exact duplicate: same type, identical normalized content
    m1 = MemoryItem(content="Prefer dark theme in IDE", type=MemoryType.PREFERENCE)
    m2 = MemoryItem(content="prefer dark theme in ide", type=MemoryType.PREFERENCE)
    assert await manager.classify_duplicate(m1, m2) == DuplicateClassification.EXACT_DUPLICATE

    # Possible duplicate: same type, high token Jaccard (>= 0.75)
    m3 = MemoryItem(content="Prefer dark theme in modern IDE", type=MemoryType.PREFERENCE)
    m4 = MemoryItem(content="Prefer dark theme in IDE", type=MemoryType.PREFERENCE)
    assert await manager.classify_duplicate(m3, m4) == DuplicateClassification.POSSIBLE_DUPLICATE

    # Possible duplicate: preference key collision (shares key tags)
    m5 = MemoryItem(content="Prefer dark theme", type=MemoryType.PREFERENCE, tags=["ui", "theme"])
    m6 = MemoryItem(content="Prefer light theme", type=MemoryType.PREFERENCE, tags=["ui", "theme"])
    assert await manager.classify_duplicate(m5, m6) == DuplicateClassification.POSSIBLE_DUPLICATE

    # No duplicate: disjoint content, or different types
    m7 = MemoryItem(content="Flight to Paris at 8am", type=MemoryType.TASK)
    m8 = MemoryItem(content="Favorite food is sushi", type=MemoryType.PREFERENCE)
    assert await manager.classify_duplicate(m7, m8) == DuplicateClassification.NO_DUPLICATE


# ══════════════════════════════════════════════════════════════════════════════
# Group H: Conflict Behavior (Read-Only Invariant)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_conflicting_memories_preserved_without_autonomous_resolution():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    # Store two contradictory facts
    f1, _ = await manager.create_memory_for_user(
        user_id="u_conflict",
        content="User lives in New York City",
        type=MemoryType.FACT,
    )
    f2, _ = await manager.create_memory_for_user(
        user_id="u_conflict",
        content="User lives in San Francisco California",
        type=MemoryType.FACT,
    )

    # Retrieval should return both if query matches
    results = await manager.retrieve_memories(user_id="u_conflict", query="User lives in")
    ids = [r.id for r in results]
    assert f1.id in ids
    assert f2.id in ids

    # Neither memory is deleted, archived, or mutated
    mem1 = await store.get(f1.id)
    mem2 = await store.get(f2.id)
    assert mem1 is not None and mem1.is_active is True
    assert mem2 is not None and mem2.is_active is True


# ══════════════════════════════════════════════════════════════════════════════
# Group I: Strict User Isolation & Legacy Scoping
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_user_isolation_strict():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    # User A memory
    await manager.create_memory_for_user(
        user_id="user_alpha",
        content="Secret project alpha roadmap",
        type=MemoryType.FACT,
    )
    # User B memory
    await manager.create_memory_for_user(
        user_id="user_beta",
        content="Secret project beta roadmap",
        type=MemoryType.FACT,
    )

    # User Alpha cannot see Beta's memories
    alpha_results = await manager.retrieve_memories(user_id="user_alpha", query="Secret project")
    assert len(alpha_results) == 1
    assert "alpha" in alpha_results[0].content
    assert "beta" not in alpha_results[0].content

    # Empty user_id fails closed with ValueError
    with pytest.raises(ValueError):
        await manager.retrieve_memories(user_id="", query="Secret")


@pytest.mark.asyncio
async def test_unscoped_legacy_record_behavior():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    # Legacy memory without user_id in metadata
    legacy_item = MemoryItem(
        id="mem_unscoped_legacy",
        content="Legacy unscoped operational guideline",
        type=MemoryType.FACT,
        metadata={},  # No user_id
    )
    await store.put(legacy_item)

    # 1. Normal user query fails closed: cannot see unscoped memory
    res_normal = await manager.retrieve_memories(user_id="user_alpha", query="operational guideline")
    assert len(res_normal) == 0

    # 2. DEFAULT_LOCAL_USER_ID cannot see unscoped memory normally
    from app.services.identity import DEFAULT_LOCAL_USER_ID
    res_default_user = await manager.retrieve_memories(user_id=DEFAULT_LOCAL_USER_ID, query="operational guideline")
    assert len(res_default_user) == 0

    # 3. Explicit internal test helper CAN retrieve it
    res_internal = await manager.retrieve_memories_unscoped_internal(query="operational guideline")
    assert len(res_internal) == 1
    assert res_internal[0].id == "mem_unscoped_legacy"


# ══════════════════════════════════════════════════════════════════════════════
# Group J: Context Engine Integration & Failure Isolation
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_context_engine_formatting_and_fencing():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)
    provider = MemoryContextProvider(memory_manager=manager)

    await manager.create_memory_for_user(
        user_id="u_context",
        content="Prefers concise summary responses",
        type=MemoryType.PREFERENCE,
    )

    session = ConversationSession(conversation_id="s1", metadata={"user_id": "u_context"})
    block = await provider.get_context(session=session, query="concise summary")

    assert block is not None
    assert "<user_memory>" in block.content
    assert "</user_memory>" in block.content
    assert "Prefers concise summary responses" in block.content


@pytest.mark.asyncio
async def test_context_engine_missing_identity_fails_closed():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)
    provider = MemoryContextProvider(memory_manager=manager)

    # Session with empty user_id in metadata
    session = ConversationSession(conversation_id="s_anon", metadata={"user_id": ""})
    block = await provider.get_context(session=session, query="test")
    assert block is None


@pytest.mark.asyncio
async def test_context_engine_failure_isolation():
    # If retriever/store raises an unexpected error, provider must catch it and return None
    class FailingStore(InMemoryMemoryStore):
        async def list_memories(self, *args, **kwargs):
            raise RuntimeError("Database connection error")

    failing_manager = MemoryManager(store=FailingStore())
    provider = MemoryContextProvider(memory_manager=failing_manager)

    session = ConversationSession(conversation_id="s1", metadata={"user_id": "u1"})
    # Must not raise RuntimeError, must fail closed safely
    block = await provider.get_context(session=session, query="test query")
    assert block is None


# ══════════════════════════════════════════════════════════════════════════════
# Group K: Mutation Boundary
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_mutation_boundary_retrieval_and_lifecycle_are_read_only():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    item, _ = await manager.create_memory_for_user(
        user_id="u_immut",
        content="Read only verification note",
        type=MemoryType.FACT,
        importance=4,
        confidence=0.9,
    )
    original_dict = item.model_dump()

    # Perform retrieval and ranking
    await manager.retrieve_memories(user_id="u_immut", query="verification note", now=now)
    # Evaluate lifecycle
    evaluate_lifecycle_state(item, now=now)

    persisted = await store.get(item.id)
    assert persisted.model_dump() == original_dict


# ══════════════════════════════════════════════════════════════════════════════
# Group L: API User Scoping & Legacy 404
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_api_user_isolation_and_legacy_404():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    app.dependency_overrides[get_memory_manager_dependency] = lambda: manager
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create memory as user_alpha
            resp_create = await client.post(
                "/api/v1/memories",
                json={"content": "Alpha specific data", "type": "fact"},
                headers={"X-User-ID": "user_alpha"},
            )
            assert resp_create.status_code == 201
            alpha_mem_id = resp_create.json()["id"]

            # 2. User alpha can read it
            resp_alpha_get = await client.get(
                f"/api/v1/memories/{alpha_mem_id}",
                headers={"X-User-ID": "user_alpha"},
            )
            assert resp_alpha_get.status_code == 200

            # 3. User beta attempting to read alpha's memory -> 404
            resp_beta_get = await client.get(
                f"/api/v1/memories/{alpha_mem_id}",
                headers={"X-User-ID": "user_beta"},
            )
            assert resp_beta_get.status_code == 404

            # 4. User beta attempting to PATCH alpha's memory -> 404
            resp_beta_patch = await client.patch(
                f"/api/v1/memories/{alpha_mem_id}",
                json={"content": "Hacked content"},
                headers={"X-User-ID": "user_beta"},
            )
            assert resp_beta_patch.status_code == 404

            # 5. User beta attempting to DELETE alpha's memory -> 404
            resp_beta_delete = await client.delete(
                f"/api/v1/memories/{alpha_mem_id}",
                headers={"X-User-ID": "user_beta"},
            )
            assert resp_beta_delete.status_code == 404

            # 6. Legacy unscoped memory -> 404 in normal API context
            legacy_item = MemoryItem(
                id="mem_unscoped_123",
                content="Legacy unscoped note",
                type=MemoryType.FACT,
                metadata={},  # No user_id
            )
            await store.put(legacy_item)

            resp_legacy_get = await client.get(
                f"/api/v1/memories/{legacy_item.id}",
                headers={"X-User-ID": "user_alpha"},
            )
            assert resp_legacy_get.status_code == 404

            # Unscoped memory is not listed in API list
            resp_list = await client.get(
                "/api/v1/memories",
                headers={"X-User-ID": "user_alpha"},
            )
            assert resp_list.status_code == 200
            list_ids = [m["id"] for m in resp_list.json()["items"]]
            assert legacy_item.id not in list_ids
            assert alpha_mem_id in list_ids
    finally:
        app.dependency_overrides.clear()


def test_old_permanent_memory_is_not_invalid_and_retrievable():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
    old_time = now - timedelta(days=500)
    item = MemoryItem(
        content="Fundamental user preference established long ago",
        type=MemoryType.PREFERENCE,
        created_at=old_time,
        updated_at=old_time,
        expires_at=None,
    )
    # Old != invalid: state is active, not expired
    assert evaluate_lifecycle_state(item, now=now) == MemoryLifecycleState.ACTIVE
    assert item.is_expired_at(now) is False
    # Freshness is asymptotic floor 0.50
    assert calculate_freshness(item, now=now) == 0.50


@pytest.mark.asyncio
async def test_ranking_detailed_components():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Tag matching boost (+4.0 per token)
    item_with_tag = MemoryItem(
        id="mem_tag",
        content="General documentation note",
        type=MemoryType.FACT,
        tags=["python", "fastapi"],
        importance=3,
        confidence=1.0,
        metadata={"user_id": "u1"},
    )
    item_no_tag = MemoryItem(
        id="mem_notag",
        content="General documentation note",
        type=MemoryType.FACT,
        tags=[],
        importance=3,
        confidence=1.0,
        metadata={"user_id": "u1"},
    )
    store._memories[item_with_tag.id] = item_with_tag
    store._memories[item_no_tag.id] = item_no_tag

    res_tag = await retriever.retrieve("documentation python", user_id="u1", now=now)
    assert res_tag[0].id == "mem_tag"

    # 2. Preference +1.5 bonus
    item_pref = MemoryItem(
        id="mem_pref_bonus",
        content="Database connection timeout guidelines",
        type=MemoryType.PREFERENCE,
        importance=3,
        confidence=1.0,
        metadata={"user_id": "u1"},
    )
    item_fact = MemoryItem(
        id="mem_fact_bonus",
        content="Database connection timeout guidelines",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        metadata={"user_id": "u1"},
    )
    store._memories[item_pref.id] = item_pref
    store._memories[item_fact.id] = item_fact

    res_pref = await retriever.retrieve("database connection", user_id="u1", now=now)
    assert res_pref[0].id == "mem_pref_bonus"


@pytest.mark.asyncio
async def test_tie_breaking_created_at_precedence():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    same_updated = now - timedelta(hours=1)
    t_older_created = now - timedelta(days=5)
    t_newer_created = now - timedelta(days=2)

    item_older = MemoryItem(
        id="mem_older_created",
        content="Identical matching query text",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        created_at=t_older_created,
        updated_at=same_updated,
        metadata={"user_id": "u1"},
    )
    item_newer = MemoryItem(
        id="mem_newer_created",
        content="Identical matching query text",
        type=MemoryType.FACT,
        importance=3,
        confidence=1.0,
        created_at=t_newer_created,
        updated_at=same_updated,
        metadata={"user_id": "u1"},
    )
    store._memories[item_older.id] = item_older
    store._memories[item_newer.id] = item_newer

    results = await retriever.retrieve("matching query text", user_id="u1", now=now)
    # Both have identical score and updated_at -> newer created comes first
    assert results[0].id == "mem_newer_created"
    assert results[1].id == "mem_older_created"


def test_semantic_type_modifiers_weights():
    from app.services.memory.retriever import get_semantic_type_multiplier, get_source_multiplier

    # Provenance
    assert get_source_multiplier(MemorySource.USER_EXPLICIT) == 1.10
    assert get_source_multiplier(MemorySource.INFERRED) == 1.00
    assert get_source_multiplier(MemorySource.SYSTEM) == 1.00

    # Semantic types
    assert get_semantic_type_multiplier(MemoryType.PREFERENCE, "any query") == 1.15
    assert get_semantic_type_multiplier(MemoryType.FACT, "any query") == 1.05
    assert get_semantic_type_multiplier(MemoryType.COMMITMENT, "any query") == 1.00
    assert get_semantic_type_multiplier(MemoryType.TASK, "any query") == 1.00
    # EPISODIC without temporal anchor remains 0.95 even with temporal query words
    assert get_semantic_type_multiplier(MemoryType.EPISODIC, "standard query") == 0.95
    assert get_semantic_type_multiplier(MemoryType.EPISODIC, "what happened yesterday?") == 0.95
    assert get_semantic_type_multiplier(MemoryType.EPISODIC, "remember when we met") == 0.95

    # Temporally anchored EPISODIC gets 1.15
    anchored_item = MemoryItem(
        content="Meeting with team yesterday",
        type=MemoryType.EPISODIC,
        metadata={"temporal_anchor": "yesterday"},
    )
    assert get_semantic_type_multiplier(MemoryType.EPISODIC, "any query", item=anchored_item) == 1.15

    assert get_semantic_type_multiplier(MemoryType.EXPLICIT, "any query") == 1.00


# ══════════════════════════════════════════════════════════════════════════════
# Group M: Phase 5L.3 Final Hardening & Boundary Verifications
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_strict_user_scoped_manager_methods_fail_closed():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    # 1. retrieve_memories fails closed on empty / None / whitespace user_id
    with pytest.raises(ValueError):
        await manager.retrieve_memories(user_id="", query="test")
    with pytest.raises(ValueError):
        await manager.retrieve_memories(user_id="   ", query="test")
    with pytest.raises(ValueError):
        await manager.retrieve_memories(user_id=None, query="test")

    # 2. list_memories_for_user fails closed
    with pytest.raises(ValueError):
        await manager.list_memories_for_user(user_id="")
    with pytest.raises(ValueError):
        await manager.list_memories_for_user(user_id="   ")

    # 3. count_memories_for_user fails closed
    with pytest.raises(ValueError):
        await manager.count_memories_for_user(user_id="")

    # 4. get_memory_for_user fails closed
    with pytest.raises(ValueError):
        await manager.get_memory_for_user(user_id="", memory_id="mem_1")

    # 5. delete_memory_for_user fails closed
    with pytest.raises(ValueError):
        await manager.delete_memory_for_user(user_id="", memory_id="mem_1")

    # 6. create_memory_for_user fails closed
    with pytest.raises(ValueError):
        await manager.create_memory_for_user(user_id="", content="Content")


@pytest.mark.asyncio
async def test_api_rejects_body_user_id_override():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    app.dependency_overrides[get_memory_manager_dependency] = lambda: manager
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. POST /api/v1/memories with user_id in body must fail with 422 Unprocessable Entity (extra='forbid')
            resp_create = await client.post(
                "/api/v1/memories",
                json={
                    "content": "Malicious attempt to set foreign user_id",
                    "type": "fact",
                    "user_id": "usr_victim_123",
                },
                headers={"X-User-ID": "usr_attacker"},
            )
            assert resp_create.status_code == 422

            # 2. Valid creation without user_id in body succeeds
            resp_valid = await client.post(
                "/api/v1/memories",
                json={
                    "content": "Valid user content",
                    "type": "fact",
                },
                headers={"X-User-ID": "usr_legit"},
            )
            assert resp_valid.status_code == 201
            legit_id = resp_valid.json()["id"]

            # 3. PATCH /api/v1/memories/{id} with user_id in body must fail with 422 Unprocessable Entity
            resp_patch = await client.patch(
                f"/api/v1/memories/{legit_id}",
                json={
                    "user_id": "usr_new_owner",
                },
                headers={"X-User-ID": "usr_legit"},
            )
            assert resp_patch.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_context_provider_missing_identity_no_fallback_to_default():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)
    provider = MemoryContextProvider(memory_manager=manager)

    from app.services.identity import DEFAULT_LOCAL_USER_ID

    # Store a memory for the default local user
    await manager.create_memory_for_user(
        user_id=DEFAULT_LOCAL_USER_ID,
        content="Default user sensitive memory",
        type=MemoryType.PREFERENCE,
    )

    # Session completely missing user_id in metadata
    session_no_id = ConversationSession(conversation_id="s_no_id", metadata={})
    block = await provider.get_context(session=session_no_id, query="sensitive memory")
    assert block is None, "Provider must return None and NOT fall back to DEFAULT_LOCAL_USER_ID"

    # Session with None user_id
    session_none = ConversationSession(conversation_id="s_none", metadata={"user_id": None})
    block_none = await provider.get_context(session=session_none, query="sensitive memory")
    assert block_none is None, "Provider must return None when user_id is None"


def test_expiration_exact_boundary_conditions():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # 1. expires_at is None -> never expires
    item_never = MemoryItem(content="Permanent", type=MemoryType.FACT, expires_at=None)
    assert item_never.is_expired_at(now) is False
    assert evaluate_lifecycle_state(item_never, now=now) == MemoryLifecycleState.ACTIVE

    # 2. now < expires_at -> active
    item_future = MemoryItem(content="Future", type=MemoryType.FACT, expires_at=now + timedelta(seconds=1))
    assert item_future.is_expired_at(now) is False
    assert evaluate_lifecycle_state(item_future, now=now) == MemoryLifecycleState.ACTIVE

    # 3. now == expires_at -> STILL ACTIVE (is_expired_at requires now > expires_at)
    item_exact = MemoryItem(content="Exact boundary", type=MemoryType.FACT, expires_at=now)
    assert item_exact.is_expired_at(now) is False, "now == expires_at must STILL be ACTIVE"
    assert evaluate_lifecycle_state(item_exact, now=now) == MemoryLifecycleState.ACTIVE

    # 4. now > expires_at -> expired
    item_past = MemoryItem(content="Past", type=MemoryType.FACT, expires_at=now - timedelta(microseconds=1))
    assert item_past.is_expired_at(now) is True
    assert evaluate_lifecycle_state(item_past, now=now) == MemoryLifecycleState.EXPIRED


def test_freshness_edge_cases_exact_boundaries():
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Permanent: floor at 0.50
    ancient_time = now - timedelta(days=5000)
    item_permanent_ancient = MemoryItem(
        content="Ancient note",
        type=MemoryType.PREFERENCE,
        created_at=ancient_time,
        updated_at=ancient_time,
        expires_at=None,
    )
    freshness_ancient = calculate_freshness(item_permanent_ancient, now=now)
    assert freshness_ancient == 0.50, "Permanent memory freshness must never fall below 0.50 floor"

    # Fresh permanent memory
    item_permanent_fresh = MemoryItem(
        content="Fresh note",
        type=MemoryType.PREFERENCE,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    assert calculate_freshness(item_permanent_fresh, now=now) == 1.0

    # 2. Expiring: full remaining lifespan -> 1.0
    created = now - timedelta(days=1)
    expires = now + timedelta(days=9)  # total 10 days, remaining 9 days -> 0.20 + 0.80 * (9/10) = 0.92
    item_expiring = MemoryItem(
        content="Expiring task",
        type=MemoryType.TASK,
        created_at=created,
        updated_at=created,
        expires_at=expires,
    )
    freshness_expiring = calculate_freshness(item_expiring, now=now)
    assert abs(freshness_expiring - 0.92) < 1e-4

    # Expiring at exact expiration: remaining == 0 -> 0.20 + 0.80 * 0 = 0.20
    item_expiring_at_boundary = MemoryItem(
        content="Boundary task",
        type=MemoryType.TASK,
        created_at=created,
        updated_at=created,
        expires_at=now,
    )
    assert calculate_freshness(item_expiring_at_boundary, now=now) == 0.20

    # 3. Expired: 0.0
    item_expired = MemoryItem(
        content="Expired task",
        type=MemoryType.TASK,
        created_at=created,
        updated_at=created,
        expires_at=now - timedelta(hours=1),
    )
    assert calculate_freshness(item_expired, now=now) == 0.0


@pytest.mark.asyncio
async def test_temporal_episodic_boost_regression():
    store = InMemoryMemoryStore()
    retriever = LexicalMemoryRetriever(store=store)
    now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    # Memory A: Anchored episodic
    mem_anchored = MemoryItem(
        id="mem_anchored",
        content="Discussion about architecture decisions",
        type=MemoryType.EPISODIC,
        importance=3,
        confidence=1.0,
        metadata={"user_id": "u_test", "temporal_anchor": "yesterday"},
    )
    # Memory B: Non-anchored episodic
    mem_non_anchored = MemoryItem(
        id="mem_non_anchored",
        content="Discussion about architecture decisions",
        type=MemoryType.EPISODIC,
        importance=3,
        confidence=1.0,
        metadata={"user_id": "u_test"},
    )
    store._memories[mem_anchored.id] = mem_anchored
    store._memories[mem_non_anchored.id] = mem_non_anchored

    # Query contains temporal keyword "yesterday"
    query = "What was the discussion about architecture decisions yesterday?"

    # Score anchored memory
    score_anchored = retriever._calculate_relevance(
        query=query,
        item=mem_anchored,
        now=now,
    )
    # Score non-anchored memory
    score_non_anchored = retriever._calculate_relevance(
        query=query,
        item=mem_non_anchored,
        now=now,
    )

    # Anchored must have 1.15 multiplier vs 0.95 for non-anchored: ratio must be 1.15 / 0.95
    assert score_anchored > score_non_anchored
    ratio = score_anchored / score_non_anchored
    assert abs(ratio - (1.15 / 0.95)) < 1e-4


@pytest.mark.asyncio
async def test_duplicate_classification_strictly_read_only_zero_mutation():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    m1 = MemoryItem(
        id="m1_dupe",
        content="Prefer pytest over unittest",
        type=MemoryType.PREFERENCE,
        tags=["python", "testing"],
        importance=4,
        confidence=0.85,
        metadata={"user_id": "u_dup", "custom": "field"},
    )
    m2 = MemoryItem(
        id="m2_dupe",
        content="prefer pytest over unittest",
        type=MemoryType.PREFERENCE,
        tags=["python", "testing"],
        importance=3,
        confidence=0.90,
        metadata={"user_id": "u_dup"},
    )

    m1_snapshot = m1.model_dump()
    m2_snapshot = m2.model_dump()

    classification = await manager.classify_duplicate(m1, m2)
    assert classification == DuplicateClassification.EXACT_DUPLICATE

    # Assert zero mutation
    assert m1.model_dump() == m1_snapshot
    assert m2.model_dump() == m2_snapshot


@pytest.mark.asyncio
async def test_retrieval_contract_limits_and_budgets():
    store = InMemoryMemoryStore()
    manager = MemoryManager(store=store)

    # 1. Negative limit -> ValueError
    with pytest.raises(ValueError):
        await manager.retrieve_memories(user_id="u_lim", query="test", limit=-1)

    # 2. Limit 0 -> empty list []
    res_zero = await manager.retrieve_memories(user_id="u_lim", query="test", limit=0)
    assert res_zero == []

    # 3. Limit clamping to 20
    for i in range(25):
        await manager.create_memory_for_user(
            user_id="u_lim",
            content=f"Common keyword item number {i}",
            type=MemoryType.FACT,
        )
    res_clamped = await manager.retrieve_memories(user_id="u_lim", query="Common keyword item", limit=50)
    assert len(res_clamped) == 20


