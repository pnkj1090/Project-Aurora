"""
Phase 5H — Backend unit tests for Proactive Models, Store, Policy, and Manager.
"""
from datetime import datetime, timezone, timedelta
import pytest
from pydantic import ValidationError

from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveStatus,
    ProactiveTrigger,
    ProactiveAction,
    ProactivePriority,
    DEFAULT_PROACTIVE_TTL_SECONDS,
    ALLOWED_PROACTIVE_SOURCES,
)
from app.services.proactive.store import InMemoryProactiveStore
from app.services.proactive.policy import ProactivePolicy
from app.services.proactive.manager import ProactiveManager
from app.services.permissions.models import PermissionType, PermissionStatus, PermissionRecord
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_perm_manager() -> PermissionManager:
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


async def _grant_permission(perm_mgr: PermissionManager, user_id: str, perm: PermissionType):
    await perm_mgr.store.set_permission(
        PermissionRecord(
            permission_type=perm,
            status=PermissionStatus.AUTHORIZED,
            user_id=user_id,
        )
    )


async def _deny_permission(perm_mgr: PermissionManager, user_id: str, perm: PermissionType):
    await perm_mgr.store.set_permission(
        PermissionRecord(
            permission_type=perm,
            status=PermissionStatus.DENIED,
            user_id=user_id,
        )
    )


def _make_candidate(user_id: str = "user-a", **kwargs) -> ProactiveCandidate:
    now = datetime.now(timezone.utc)
    base = dict(
        user_id=user_id,
        title="Check traffic",
        message="Traffic is heavy on your usual route.",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.OPEN_TASK,
        priority=ProactivePriority.MEDIUM,
        source="system",
        reference_id="route-123",
        created_at=now,
        expires_at=now + timedelta(seconds=DEFAULT_PROACTIVE_TTL_SECONDS),
    )
    base.update(kwargs)
    return ProactiveCandidate(**base)


# ── Model Tests ───────────────────────────────────────────────────────────────

def test_model_valid():
    candidate = _make_candidate()
    assert candidate.title == "Check traffic"
    assert candidate.status == ProactiveStatus.PENDING
    assert not candidate.is_expired
    assert candidate.ttl_remaining_seconds > 0
    assert candidate.dedup_key == ("user-a", "time", "system", "route-123", "open_task")


def test_model_title_message_whitespace_stripped():
    candidate = _make_candidate(title="  Review Schedule  ", message="  3 meetings today.  ")
    assert candidate.title == "Review Schedule"
    assert candidate.message == "3 meetings today."


def test_model_empty_title_rejected():
    with pytest.raises(ValidationError):
        _make_candidate(title="   ")


def test_model_empty_message_rejected():
    with pytest.raises(ValidationError):
        _make_candidate(message="")


def test_model_source_allowlist():
    for src in ALLOWED_PROACTIVE_SOURCES:
        c = _make_candidate(source=src)
        assert c.source == src

    with pytest.raises(ValidationError):
        _make_candidate(source="untrusted_external_crawler")


def test_model_naive_datetime_rejected():
    naive_dt = datetime.now()
    with pytest.raises(ValidationError):
        _make_candidate(created_at=naive_dt)

    with pytest.raises(ValidationError):
        _make_candidate(expires_at=naive_dt)


def test_model_is_expired():
    now = datetime.now(timezone.utc)
    expired_candidate = _make_candidate(
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )
    assert expired_candidate.is_expired
    assert expired_candidate.ttl_remaining_seconds == 0


# ── Store Tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_store_crud_and_user_isolation():
    store = InMemoryProactiveStore()
    c_a = _make_candidate(user_id="user-a", title="User A Cand")
    c_b = _make_candidate(user_id="user-b", title="User B Cand")

    await store.add(c_a)
    await store.add(c_b)

    assert await store.get(c_a.candidate_id, "user-a") is not None
    assert await store.get(c_b.candidate_id, "user-a") is None

    assert await store.get(c_b.candidate_id, "user-b") is not None
    assert await store.get(c_a.candidate_id, "user-b") is None

    a_list = await store.list(user_id="user-a")
    assert len(a_list) == 1
    assert a_list[0].candidate_id == c_a.candidate_id

    b_list = await store.list(user_id="user-b")
    assert len(b_list) == 1
    assert b_list[0].candidate_id == c_b.candidate_id


@pytest.mark.asyncio
async def test_store_lazy_expiration():
    store = InMemoryProactiveStore()
    now = datetime.now(timezone.utc)
    expired_c = _make_candidate(
        user_id="user-a",
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )
    await store.add(expired_c)

    retrieved = await store.get(expired_c.candidate_id, "user-a")
    assert retrieved is not None
    assert retrieved.status == ProactiveStatus.EXPIRED

    removed = store.sweep_expired("user-a")
    assert removed == 1
    assert await store.get(expired_c.candidate_id, "user-a") is None


# ── Policy & Deduplication Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_order_dedup_before_rate_limit():
    """Deduplication MUST happen before rate limiting. Duplicate does not consume quota."""
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    c1 = _make_candidate(user_id="user-a", title="Task 1", reference_id="ref-1")
    eval1 = await policy.evaluate(c1, store=store)
    assert eval1.status == ProactiveStatus.ELIGIBLE
    await store.add(eval1)

    assert store.count_created_in_window("user-a", 3600) == 1

    c2 = _make_candidate(user_id="user-a", title="Task 1", reference_id="ref-1")
    eval2 = await policy.evaluate(c2, store=store)
    assert eval2.status == ProactiveStatus.SUPPRESSED
    assert eval2.suppression_reason == "deduplicated"

    # Quota still 1, not 2!
    assert store.count_created_in_window("user-a", 3600) == 1


@pytest.mark.asyncio
async def test_policy_terminal_states_block_duplicates_while_unexpired():
    """DISMISSED and CANCELLED candidates also block duplicates while unexpired."""
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    c1 = _make_candidate(user_id="user-a", title="Meeting Soon", reference_id="meet-1")
    eval1 = await policy.evaluate(c1, store=store)
    eval1.status = ProactiveStatus.DISMISSED
    await store.add(eval1)

    c2 = _make_candidate(user_id="user-a", title="Meeting Soon", reference_id="meet-1")
    eval2 = await policy.evaluate(c2, store=store)
    assert eval2.status == ProactiveStatus.SUPPRESSED
    assert eval2.suppression_reason == "deduplicated"


@pytest.mark.asyncio
async def test_policy_expired_candidate_does_not_block_duplicate():
    """EXPIRED candidates no longer block duplicates."""
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    now = datetime.now(timezone.utc)
    expired_c = _make_candidate(
        user_id="user-a",
        title="Old Alert",
        reference_id="alert-1",
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
        status=ProactiveStatus.EXPIRED,
    )
    await store.add(expired_c)

    new_c = _make_candidate(user_id="user-a", title="Old Alert", reference_id="alert-1")
    eval_new = await policy.evaluate(new_c, store=store)
    assert eval_new.status == ProactiveStatus.ELIGIBLE


# ── Rate Limiting Tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_rate_limit_5_per_hour():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    for i in range(5):
        c = _make_candidate(user_id="user-a", title=f"Unique Task {i}", reference_id=f"ref-{i}")
        evaluated = await policy.evaluate(c, store=store)
        assert evaluated.status == ProactiveStatus.ELIGIBLE
        await store.add(evaluated)

    assert store.count_created_in_window("user-a", 3600) == 5

    c6 = _make_candidate(user_id="user-a", title="Unique Task 6", reference_id="ref-6")
    eval6 = await policy.evaluate(c6, store=store)
    assert eval6.status == ProactiveStatus.SUPPRESSED
    assert eval6.suppression_reason == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_policy_independent_user_quotas():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    for i in range(5):
        c = _make_candidate(user_id="user-a", title=f"Task {i}", reference_id=f"a-{i}")
        eval_c = await policy.evaluate(c, store=store)
        assert eval_c.status == ProactiveStatus.ELIGIBLE
        await store.add(eval_c)

    c_b = _make_candidate(user_id="user-b", title="Task B0", reference_id="b-0")
    eval_b = await policy.evaluate(c_b, store=store)
    assert eval_b.status == ProactiveStatus.ELIGIBLE


# ── Permission Enforcement Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_calendar_permission():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    await _deny_permission(perm_mgr, "user-a", PermissionType.CALENDAR)

    c = _make_candidate(
        user_id="user-a",
        trigger=ProactiveTrigger.CALENDAR_EVENT,
        source="system",
        title="Calendar Notice",
    )
    eval_c = await policy.evaluate(c, store=store)
    assert eval_c.status == ProactiveStatus.SUPPRESSED
    assert eval_c.suppression_reason == "permission_unauthorized"

    await _grant_permission(perm_mgr, "user-a", PermissionType.CALENDAR)
    eval_c_granted = await policy.evaluate(c, store=store)
    assert eval_c_granted.status == ProactiveStatus.ELIGIBLE


@pytest.mark.asyncio
async def test_policy_location_permission():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    await _deny_permission(perm_mgr, "user-a", PermissionType.LOCATION)

    c = _make_candidate(
        user_id="user-a",
        trigger=ProactiveTrigger.LOCATION_CONTEXT,
        source="system",
        title="Location Notice",
    )
    eval_c = await policy.evaluate(c, store=store)
    assert eval_c.status == ProactiveStatus.SUPPRESSED
    assert eval_c.suppression_reason == "permission_unauthorized"

    await _grant_permission(perm_mgr, "user-a", PermissionType.LOCATION)
    eval_c_granted = await policy.evaluate(c, store=store)
    assert eval_c_granted.status == ProactiveStatus.ELIGIBLE


@pytest.mark.asyncio
async def test_policy_notification_permission():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)

    await _deny_permission(perm_mgr, "user-a", PermissionType.NOTIFICATIONS)

    c = _make_candidate(
        user_id="user-a",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.NOTIFY,
        title="Urgent Reminder",
    )
    eval_c = await policy.evaluate(c, store=store)
    assert eval_c.status == ProactiveStatus.SUPPRESSED
    assert eval_c.suppression_reason == "permission_unauthorized"

    await _grant_permission(perm_mgr, "user-a", PermissionType.NOTIFICATIONS)
    eval_c_granted = await policy.evaluate(c, store=store)
    assert eval_c_granted.status == ProactiveStatus.ELIGIBLE


# ── Manager Tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manager_lifecycle():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)

    candidate = await manager.create_candidate(
        user_id="user-a",
        title="Leave now",
        message="Leave now to arrive on time.",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.OPEN_TASK,
        priority=ProactivePriority.HIGH,
        source="system",
    )
    assert candidate.status == ProactiveStatus.ELIGIBLE
    assert candidate.created_at is not None
    assert candidate.expires_at is not None
    delta = (candidate.expires_at - candidate.created_at).total_seconds()
    assert abs(delta - DEFAULT_PROACTIVE_TTL_SECONDS) < 2

    dismissed = await manager.dismiss_candidate(candidate.candidate_id, user_id="user-a")
    assert dismissed is not None
    assert dismissed.status == ProactiveStatus.DISMISSED

    c2 = await manager.create_candidate(
        user_id="user-a",
        title="Another alert",
        message="Details here",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.OPEN_TASK,
        source="system",
    )
    cancelled = await manager.cancel_candidate(c2.candidate_id, user_id="user-a")
    assert cancelled is not None
    assert cancelled.status == ProactiveStatus.CANCELLED
