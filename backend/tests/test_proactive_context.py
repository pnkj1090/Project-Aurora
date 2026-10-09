"""
Phase 5H — Unit tests for ProactiveContextProvider.
"""
from datetime import datetime, timezone, timedelta
import pytest

from app.services.context.base import ContextBlock, ContextSensitivity
from app.services.context.providers.proactive import ProactiveContextProvider
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveStatus,
    ProactiveTrigger,
    ProactiveAction,
    ProactivePriority,
    DEFAULT_PROACTIVE_TTL_SECONDS,
)
from app.services.proactive.store import InMemoryProactiveStore
from app.services.proactive.policy import ProactivePolicy
from app.services.proactive.manager import ProactiveManager
from tests.test_proactive import _make_perm_manager


def _make_candidate(user_id: str = "user-a", idx: int = 1, status: ProactiveStatus = ProactiveStatus.ELIGIBLE) -> ProactiveCandidate:
    now = datetime.now(timezone.utc)
    return ProactiveCandidate(
        user_id=user_id,
        title=f"Proactive item {idx}",
        message=f"Detailed message for candidate {idx}",
        trigger=ProactiveTrigger.LOCATION_CONTEXT,
        action=ProactiveAction.OPEN_TASK,
        priority=ProactivePriority.MEDIUM,
        source="system",
        reference_id=f"secret-ref-id-{idx}",
        metadata={"internal_debug_key": "confidential_value"},
        status=status,
        created_at=now,
        expires_at=now + timedelta(seconds=DEFAULT_PROACTIVE_TTL_SECONDS),
    )


@pytest.mark.asyncio
async def test_proactive_context_strictly_opt_in():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)
    provider = ProactiveContextProvider(manager)

    # Add eligible candidate
    await store.add(_make_candidate("user-a", 1, ProactiveStatus.ELIGIBLE))

    # Generic conversation queries should return None
    generic_queries = [
        "Hello Aurora",
        "What is the weather today?",
        "Can you help me write an email?",
        "Tell me a joke",
        "Schedule lunch with Bob tomorrow",
    ]
    for q in generic_queries:
        block = await provider.get_context(query=q, user_id="user-a")
        assert block is None, f"Expected None for generic query: {q}"

    # Explicit proactive intent queries should return a ContextBlock
    opt_in_queries = [
        "What proactive suggestions do you have?",
        "Show my proactive suggestions",
        "Do I have any proactive recommendations?",
        "Any proactive reminders?",
    ]
    for q in opt_in_queries:
        block = await provider.get_context(query=q, user_id="user-a")
        assert block is not None, f"Expected context for explicit query: {q}"
        assert block.sensitivity == ContextSensitivity.PRIVATE
        assert block.source == "system/proactive"


@pytest.mark.asyncio
async def test_proactive_context_only_eligible_candidates():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)
    provider = ProactiveContextProvider(manager)

    # Add candidates with different statuses
    await store.add(_make_candidate("user-a", 1, ProactiveStatus.ELIGIBLE))
    await store.add(_make_candidate("user-a", 2, ProactiveStatus.PENDING))
    await store.add(_make_candidate("user-a", 3, ProactiveStatus.SUPPRESSED))
    await store.add(_make_candidate("user-a", 4, ProactiveStatus.DISMISSED))
    await store.add(_make_candidate("user-a", 5, ProactiveStatus.CANCELLED))
    await store.add(_make_candidate("user-a", 6, ProactiveStatus.EXPIRED))

    block = await provider.get_context(query="What proactive suggestions do I have?", user_id="user-a")
    assert block is not None
    # Only item 1 is ELIGIBLE
    assert "Proactive item 1" in block.content
    assert "Proactive item 2" not in block.content
    assert "Proactive item 3" not in block.content
    assert "Proactive item 4" not in block.content
    assert "Proactive item 5" not in block.content
    assert "Proactive item 6" not in block.content


@pytest.mark.asyncio
async def test_proactive_context_max_5_bounded():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)
    provider = ProactiveContextProvider(manager)

    # Add 7 eligible candidates
    for i in range(1, 8):
        await store.add(_make_candidate("user-a", i, ProactiveStatus.ELIGIBLE))

    block = await provider.get_context(query="Show proactive suggestions", user_id="user-a")
    assert block is not None
    # Context should contain at most 5 items
    count = sum(1 for i in range(1, 8) if f"Proactive item {i}" in block.content)
    assert count <= 5


@pytest.mark.asyncio
async def test_proactive_context_no_leakage_of_reference_id_or_metadata():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)
    provider = ProactiveContextProvider(manager)

    await store.add(_make_candidate("user-a", 1, ProactiveStatus.ELIGIBLE))

    block = await provider.get_context(query="Check proactive suggestions", user_id="user-a")
    assert block is not None
    assert "secret-ref-id-1" not in block.content
    assert "reference_id" not in block.content
    assert "confidential_value" not in block.content
    assert "internal_debug_key" not in block.content
    assert "metadata" not in block.content
