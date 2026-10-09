"""
Phase 5L.2 — In-memory candidate review store and failure-safe claim state machine.

Core Invariants:
1. CandidateReviewStore is strictly ephemeral. Zero SQLite tables, zero migrations.
2. User-partitioned storage: Cross-user access is impossible.
3. Concurrency-safe: Atomic claim ensures two simultaneous requests cannot both approve the same candidate.
4. Persistence failure rolls RESOLVING back to PENDING_REVIEW.
5. Successful resolution removes candidate and records in bounded idempotency ledger (max 100 per user).
6. Lazy expiration only: No background threads, loops, or schedulers.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.services.memory.candidates.models import MemoryCandidate


class CandidateReviewStatus(str, Enum):
    """Active states for candidates in the review queue."""
    PENDING_REVIEW = "pending_review"  # Awaiting user decision
    RESOLVING = "resolving"            # Claimed by in-flight approval request


class ReviewableCandidate(BaseModel):
    """
    Review-queue wrapper around a validated MemoryCandidate.
    Maintains review status, optional server-detected conflict info, and TTL.
    """
    candidate: MemoryCandidate
    status: CandidateReviewStatus = CandidateReviewStatus.PENDING_REVIEW
    conflicts_with_memory_id: Optional[str] = None
    existing_conflicting_content: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=7)
    )

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


class ResolvedLedgerEntry(BaseModel):
    """
    Minimal, bounded in-memory idempotency record for resolved candidates.
    Does NOT store raw transcripts, chain-of-thought, or web content.
    """
    candidate_id: str
    action: str  # "approved" or "rejected"
    memory_id: Optional[str] = None
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BaseCandidateReviewStore(ABC):
    """Abstract interface for candidate review queue."""

    @abstractmethod
    async def add(self, item: ReviewableCandidate) -> ReviewableCandidate:
        pass

    @abstractmethod
    async def get(self, user_id: str, candidate_id: str) -> Optional[ReviewableCandidate]:
        pass

    @abstractmethod
    async def list(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> Tuple[List[ReviewableCandidate], int]:
        pass

    @abstractmethod
    async def claim_candidate(self, user_id: str, candidate_id: str) -> Optional[ReviewableCandidate]:
        pass

    @abstractmethod
    async def commit_resolution(
        self, user_id: str, candidate_id: str, action: str, memory_id: Optional[str] = None
    ) -> None:
        pass

    @abstractmethod
    async def rollback_claim(self, user_id: str, candidate_id: str) -> None:
        pass

    @abstractmethod
    async def get_ledger_entry(self, user_id: str, candidate_id: str) -> Optional[ResolvedLedgerEntry]:
        pass

    @abstractmethod
    async def clear_for_user(self, user_id: str) -> None:
        pass


class InMemoryCandidateReviewStore(BaseCandidateReviewStore):
    """
    Thread-safe, ephemeral in-memory candidate review store with atomic claim,
    lazy TTL expiration, and bounded idempotency ledger.
    """

    def __init__(self, ttl_days: int = 7, max_ledger_size: int = 100):
        self.ttl = timedelta(days=ttl_days)
        self.max_ledger_size = max_ledger_size
        self._store: Dict[str, Dict[str, ReviewableCandidate]] = {}
        self._ledger: Dict[str, List[ResolvedLedgerEntry]] = {}
        self._lock = asyncio.Lock()
        self._candidate_locks: Dict[str, asyncio.Lock] = {}

    def get_candidate_lock(self, user_id: str, candidate_id: str) -> asyncio.Lock:
        """Returns or creates a per-candidate lock."""
        key = f"{user_id}:{candidate_id}"
        if key not in self._candidate_locks:
            self._candidate_locks[key] = asyncio.Lock()
        return self._candidate_locks[key]

    def _cleanup_candidate_lock_locked(self, user_id: str, candidate_id: str) -> None:
        """Cleans up lock when candidate reaches terminal state (approved/rejected/expired/cleared)."""
        key = f"{user_id}:{candidate_id}"
        self._candidate_locks.pop(key, None)

    def _sweep_expired_locked(self, user_id: str) -> None:
        """Removes expired candidates for user under active lock and cleans up their locks."""
        user_candidates = self._store.get(user_id)
        if not user_candidates:
            return
        now = datetime.now(timezone.utc)
        expired_ids = [
            cid for cid, item in user_candidates.items()
            if now > item.expires_at and item.status != CandidateReviewStatus.RESOLVING
        ]
        for cid in expired_ids:
            del user_candidates[cid]
            self._cleanup_candidate_lock_locked(user_id, cid)

    async def add(self, item: ReviewableCandidate) -> ReviewableCandidate:
        async with self._lock:
            uid = item.candidate.user_id
            if uid not in self._store:
                self._store[uid] = {}
            self._sweep_expired_locked(uid)
            self._store[uid][item.candidate.candidate_id] = item.model_copy(deep=True)
            return item.model_copy(deep=True)

    async def get(self, user_id: str, candidate_id: str) -> Optional[ReviewableCandidate]:
        async with self._lock:
            self._sweep_expired_locked(user_id)
            user_candidates = self._store.get(user_id, {})
            item = user_candidates.get(candidate_id)
            if item is None or item.is_expired:
                return None
            return item.model_copy(deep=True)

    async def list(
        self, user_id: str, limit: int = 20, offset: int = 0
    ) -> Tuple[List[ReviewableCandidate], int]:
        async with self._lock:
            self._sweep_expired_locked(user_id)
            user_candidates = self._store.get(user_id, {})
            # Return active PENDING_REVIEW items, sorted by created_at desc
            active_items = [
                item.model_copy(deep=True)
                for item in user_candidates.values()
                if item.status == CandidateReviewStatus.PENDING_REVIEW and not item.is_expired
            ]
            active_items.sort(key=lambda x: x.created_at, reverse=True)
            total = len(active_items)
            page = active_items[offset : offset + limit]
            return page, total

    async def claim_candidate(self, user_id: str, candidate_id: str) -> Optional[ReviewableCandidate]:
        """
        Atomically transitions a candidate from PENDING_REVIEW to RESOLVING.
        Returns candidate if claim succeeded; None if already claimed, expired, or missing.
        """
        async with self._lock:
            self._sweep_expired_locked(user_id)
            user_candidates = self._store.get(user_id, {})
            item = user_candidates.get(candidate_id)
            if item is None or item.is_expired:
                return None
            if item.status != CandidateReviewStatus.PENDING_REVIEW:
                return None

            item.status = CandidateReviewStatus.RESOLVING
            return item.model_copy(deep=True)

    async def commit_resolution(
        self, user_id: str, candidate_id: str, action: str, memory_id: Optional[str] = None
    ) -> None:
        """
        Finalizes candidate resolution: Removes from active store and appends
        to the bounded resolution ledger.
        """
        async with self._lock:
            user_candidates = self._store.get(user_id, {})
            user_candidates.pop(candidate_id, None)
            self._cleanup_candidate_lock_locked(user_id, candidate_id)

            if user_id not in self._ledger:
                self._ledger[user_id] = []

            ledger = self._ledger[user_id]
            ledger.append(
                ResolvedLedgerEntry(
                    candidate_id=candidate_id,
                    action=action,
                    memory_id=memory_id,
                )
            )
            # Enforce bounded ledger size
            if len(ledger) > self.max_ledger_size:
                self._ledger[user_id] = ledger[-self.max_ledger_size:]

    async def rollback_claim(self, user_id: str, candidate_id: str) -> None:
        """
        Rolls back a failed claim from RESOLVING back to PENDING_REVIEW
        so user can retry.
        """
        async with self._lock:
            user_candidates = self._store.get(user_id, {})
            item = user_candidates.get(candidate_id)
            if item and item.status == CandidateReviewStatus.RESOLVING:
                item.status = CandidateReviewStatus.PENDING_REVIEW

    async def get_ledger_entry(self, user_id: str, candidate_id: str) -> Optional[ResolvedLedgerEntry]:
        async with self._lock:
            ledger = self._ledger.get(user_id, [])
            for entry in reversed(ledger):
                if entry.candidate_id == candidate_id:
                    return entry.model_copy(deep=True)
            return None

    async def clear_for_user(self, user_id: str) -> None:
        async with self._lock:
            self._store.pop(user_id, None)
            self._ledger.pop(user_id, None)
            keys_to_clean = [k for k in self._candidate_locks if k.startswith(f"{user_id}:")]
            for k in keys_to_clean:
                self._candidate_locks.pop(k, None)


# Default singleton instance for runtime dependency injection
_default_candidate_review_store: Optional[BaseCandidateReviewStore] = None


def get_candidate_review_store() -> BaseCandidateReviewStore:
    global _default_candidate_review_store
    if _default_candidate_review_store is None:
        _default_candidate_review_store = InMemoryCandidateReviewStore()
    return _default_candidate_review_store
