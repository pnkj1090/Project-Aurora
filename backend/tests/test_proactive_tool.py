"""
Phase 5H — Unit tests for Proactive Tools.
"""
from datetime import datetime, timezone, timedelta
import pytest

from app.services.tools.registry import create_default_tool_registry
from app.services.tools.base import ToolRiskLevel
from app.services.tools.builtins.proactive_tool import (
    ListProactiveCandidatesTool,
    DismissProactiveCandidateTool,
)
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
        title=f"Tool Test Candidate {idx}",
        message=f"Message for candidate {idx}",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.OPEN_TASK,
        priority=ProactivePriority.HIGH,
        source="system",
        reference_id=f"internal-ref-{idx}",
        metadata={"secret_token": "abc-123"},
        status=status,
        created_at=now,
        expires_at=now + timedelta(seconds=DEFAULT_PROACTIVE_TTL_SECONDS),
    )


def test_proactive_tools_registration():
    registry = create_default_tool_registry()
    list_tool = registry.get("list_proactive_candidates")
    assert list_tool is not None
    assert list_tool.definition.risk_level == ToolRiskLevel.LOW
    assert list_tool.definition.requires_confirmation is False

    dismiss_tool = registry.get("dismiss_proactive_candidate")
    assert dismiss_tool is not None
    assert dismiss_tool.definition.risk_level == ToolRiskLevel.LOW
    assert dismiss_tool.definition.requires_confirmation is False


@pytest.mark.asyncio
async def test_list_proactive_candidates_tool_execution():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)

    c_a = _make_candidate("user-a", 1, ProactiveStatus.ELIGIBLE)
    c_b = _make_candidate("user-b", 2, ProactiveStatus.ELIGIBLE)
    await store.add(c_a)
    await store.add(c_b)

    tool = ListProactiveCandidatesTool(manager)

    result = await tool.execute({"_user_id": "user-a"})
    assert result["status"] == "success"
    assert "candidates" in result
    candidates = result["candidates"]
    assert len(candidates) == 1
    assert candidates[0]["candidate_id"] == c_a.candidate_id
    assert candidates[0]["title"] == "Tool Test Candidate 1"

    # Data minimization: no reference_id or metadata
    assert "reference_id" not in candidates[0]
    assert "metadata" not in candidates[0]
    assert "internal-ref-1" not in str(result)
    assert "abc-123" not in str(result)


@pytest.mark.asyncio
async def test_dismiss_proactive_candidate_tool_execution():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)

    c_a = _make_candidate("user-a", 1, ProactiveStatus.ELIGIBLE)
    await store.add(c_a)

    tool = DismissProactiveCandidateTool(manager)

    # Dismiss existing candidate
    result = await tool.execute({"candidate_id": c_a.candidate_id, "_user_id": "user-a"})
    assert result["status"] == "success"

    # Verify store status is updated
    updated = await store.get(c_a.candidate_id, "user-a")
    assert updated.status == ProactiveStatus.DISMISSED

    # Attempt to dismiss non-existent candidate
    result_err = await tool.execute({"candidate_id": "non-existent-id", "_user_id": "user-a"})
    assert result_err["status"] == "error"
    assert "error" in result_err


@pytest.mark.asyncio
async def test_proactive_tool_user_isolation():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)

    c_b = _make_candidate("user-b", 1, ProactiveStatus.ELIGIBLE)
    await store.add(c_b)

    # User A tries to dismiss User B's candidate
    tool = DismissProactiveCandidateTool(manager)
    result = await tool.execute({"candidate_id": c_b.candidate_id, "_user_id": "user-a"})
    assert result["status"] == "error"
    assert "error" in result

    # Candidate remains ELIGIBLE in store
    retrieved = await store.get(c_b.candidate_id, "user-b")
    assert retrieved.status == ProactiveStatus.ELIGIBLE
