"""
Phase 5H — API integration tests for /api/v1/proactive endpoints.

Uses AsyncClient + ASGI transport + dependency overrides.
"""
from datetime import datetime, timezone, timedelta
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
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
from app.services.proactive.manager import ProactiveManager, get_proactive_manager_dependency
from tests.test_proactive import _make_perm_manager

USER_A = "api-user-a"
USER_B = "api-user-b"


def _headers(user_id: str = USER_A) -> dict:
    return {"X-User-ID": user_id}


@pytest.fixture
def override_proactive_manager():
    store = InMemoryProactiveStore()
    perm_mgr = _make_perm_manager()
    policy = ProactivePolicy(permission_manager=perm_mgr)
    manager = ProactiveManager(store=store, policy=policy)

    app.dependency_overrides[get_proactive_manager_dependency] = lambda: manager
    yield manager, store
    app.dependency_overrides.pop(get_proactive_manager_dependency, None)


def _seed_candidate(
    user_id: str,
    title: str,
    priority: ProactivePriority = ProactivePriority.MEDIUM,
    created_at: datetime = None,
    candidate_id: str = None,
    status: ProactiveStatus = ProactiveStatus.ELIGIBLE,
) -> ProactiveCandidate:
    now = created_at or datetime.now(timezone.utc)
    c_kwargs = dict(
        user_id=user_id,
        title=title,
        message=f"Details for {title}",
        trigger=ProactiveTrigger.TIME,
        action=ProactiveAction.OPEN_TASK,
        priority=priority,
        source="system",
        reference_id="internal-ref-seed",
        metadata={"internal_note": "seed"},
        status=status,
        created_at=now,
        expires_at=now + timedelta(seconds=DEFAULT_PROACTIVE_TTL_SECONDS),
    )
    if candidate_id:
        c_kwargs["candidate_id"] = candidate_id
    return ProactiveCandidate(**c_kwargs)


# ── Strict Pagination Tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pagination_limits(override_proactive_manager):
    manager, store = override_proactive_manager

    # Seed 30 candidates for USER_A
    for i in range(30):
        await store.add(_seed_candidate(USER_A, f"Candidate {i:02d}"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Omitted limit defaults to 20
        resp = await client.get("/api/v1/proactive/candidates", headers=_headers(USER_A))
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 20
        assert len(data["candidates"]) == 20
        assert data["total"] == 30

        # 2. limit=1 -> 200
        resp = await client.get("/api/v1/proactive/candidates?limit=1", headers=_headers(USER_A))
        assert resp.status_code == 200
        assert len(resp.json()["candidates"]) == 1

        # 3. limit=20 -> 200
        resp = await client.get("/api/v1/proactive/candidates?limit=20", headers=_headers(USER_A))
        assert resp.status_code == 200
        assert len(resp.json()["candidates"]) == 20

        # 4. limit=50 -> 200
        resp = await client.get("/api/v1/proactive/candidates?limit=50", headers=_headers(USER_A))
        assert resp.status_code == 200
        assert len(resp.json()["candidates"]) == 30

        # 5. limit=51 -> 422 (never silently capped)
        resp = await client.get("/api/v1/proactive/candidates?limit=51", headers=_headers(USER_A))
        assert resp.status_code == 422

        # 6. limit=100 -> 422
        resp = await client.get("/api/v1/proactive/candidates?limit=100", headers=_headers(USER_A))
        assert resp.status_code == 422

        # 7. limit=0 -> 422
        resp = await client.get("/api/v1/proactive/candidates?limit=0", headers=_headers(USER_A))
        assert resp.status_code == 422

        # 8. negative limit -> 422
        resp = await client.get("/api/v1/proactive/candidates?limit=-1", headers=_headers(USER_A))
        assert resp.status_code == 422

        # 9. negative offset -> 422
        resp = await client.get("/api/v1/proactive/candidates?offset=-1", headers=_headers(USER_A))
        assert resp.status_code == 422


# ── Deterministic Ordering Tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deterministic_ordering(override_proactive_manager):
    manager, store = override_proactive_manager
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=10)
    t1 = now - timedelta(minutes=5)

    # Candidate 1: LOW priority, t0
    c1 = _seed_candidate(USER_A, "Low Priority", priority=ProactivePriority.LOW, created_at=t0, candidate_id="id-1")
    # Candidate 2: HIGH priority, t0
    c2 = _seed_candidate(USER_A, "High Alert", priority=ProactivePriority.HIGH, created_at=t0, candidate_id="id-2")
    # Candidate 3: MEDIUM priority, t0 (older)
    c3 = _seed_candidate(USER_A, "Med Alert Old", priority=ProactivePriority.MEDIUM, created_at=t0, candidate_id="id-3")
    # Candidate 4: MEDIUM priority, t1 (newer)
    c4 = _seed_candidate(USER_A, "Med Alert New", priority=ProactivePriority.MEDIUM, created_at=t1, candidate_id="id-4")

    await store.add(c1)
    await store.add(c2)
    await store.add(c3)
    await store.add(c4)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/proactive/candidates", headers=_headers(USER_A))
        assert resp.status_code == 200
        items = resp.json()["candidates"]
        # Expected order:
        # 1. c2 (HIGH)
        # 2. c4 (MEDIUM, newer t1)
        # 3. c3 (MEDIUM, older t0)
        # 4. c1 (LOW)
        assert items[0]["candidate_id"] == "id-2"
        assert items[1]["candidate_id"] == "id-4"
        assert items[2]["candidate_id"] == "id-3"
        assert items[3]["candidate_id"] == "id-1"


# ── Single Retrieval, 404, & Data Minimization Tests ─────────────────────────

@pytest.mark.asyncio
async def test_get_candidate_and_data_minimization(override_proactive_manager):
    manager, store = override_proactive_manager
    c = _seed_candidate(USER_A, "Confidential Candidate", candidate_id="cand-123")
    await store.add(c)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Valid retrieval
        resp = await client.get("/api/v1/proactive/candidates/cand-123", headers=_headers(USER_A))
        assert resp.status_code == 200
        data = resp.json()
        assert data["candidate_id"] == "cand-123"
        assert data["title"] == "Confidential Candidate"

        # Data minimization: reference_id and metadata must NOT be present
        assert "reference_id" not in data
        assert "metadata" not in data
        assert "internal-ref-seed" not in resp.text
        assert "seed" not in resp.text

        # Non-existent candidate -> 404
        resp_404 = await client.get("/api/v1/proactive/candidates/non-existent", headers=_headers(USER_A))
        assert resp_404.status_code == 404


# ── Cross-User Isolation Tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_user_isolation(override_proactive_manager):
    manager, store = override_proactive_manager
    c_a = _seed_candidate(USER_A, "User A Candidate", candidate_id="user-a-cand")
    await store.add(c_a)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # User B listing -> empty
        resp = await client.get("/api/v1/proactive/candidates", headers=_headers(USER_B))
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert len(resp.json()["candidates"]) == 0

        # User B trying to get User A's candidate -> 404
        resp_get = await client.get(f"/api/v1/proactive/candidates/{c_a.candidate_id}", headers=_headers(USER_B))
        assert resp_get.status_code == 404

        # User B trying to dismiss User A's candidate -> 404
        resp_dismiss = await client.post(f"/api/v1/proactive/candidates/{c_a.candidate_id}/dismiss", headers=_headers(USER_B))
        assert resp_dismiss.status_code == 404

        # User B trying to cancel User A's candidate -> 404
        resp_cancel = await client.post(f"/api/v1/proactive/candidates/{c_a.candidate_id}/cancel", headers=_headers(USER_B))
        assert resp_cancel.status_code == 404


# ── Dismiss & Cancel Endpoints ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dismiss_and_cancel_endpoints(override_proactive_manager):
    manager, store = override_proactive_manager
    c1 = _seed_candidate(USER_A, "Dismiss Me", candidate_id="cand-dismiss")
    c2 = _seed_candidate(USER_A, "Cancel Me", candidate_id="cand-cancel")
    await store.add(c1)
    await store.add(c2)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Dismiss
        resp_d = await client.post("/api/v1/proactive/candidates/cand-dismiss/dismiss", headers=_headers(USER_A))
        assert resp_d.status_code == 200
        assert resp_d.json()["status"] == "dismissed"

        # Cancel
        resp_c = await client.post("/api/v1/proactive/candidates/cand-cancel/cancel", headers=_headers(USER_A))
        assert resp_c.status_code == 200
        assert resp_c.json()["status"] == "cancelled"
