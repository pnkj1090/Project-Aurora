"""
Phase 5H — Proactive store interfaces and in-memory implementation.

Core Rules:
- Ephemeral in-memory storage only.
- aurora.db remains completely untouched; zero tables or schema changes.
- Expiration cleanup is strictly on-demand / lazy. Zero background schedulers,
  zero timers, zero daemon processes.
- All operations are user-scoped.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveStatus,
    DEFAULT_PROACTIVE_TTL_SECONDS,
)
from app.services.identity.models import DEFAULT_LOCAL_USER_ID

logger = logging.getLogger("aurora.proactive.store")


class BaseProactiveStore(ABC):
    """Abstract interface for proactive candidate storage."""

    @abstractmethod
    async def add(self, candidate: ProactiveCandidate) -> ProactiveCandidate:
        """Store a new proactive candidate."""

    @abstractmethod
    async def get(self, candidate_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> Optional[ProactiveCandidate]:
        """Retrieve a proactive candidate by ID for the specified user."""

    @abstractmethod
    async def list(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        status: Optional[ProactiveStatus] = None,
        include_expired: bool = False,
    ) -> List[ProactiveCandidate]:
        """List proactive candidates for the specified user."""

    @abstractmethod
    async def update(self, candidate: ProactiveCandidate) -> ProactiveCandidate:
        """Update an existing candidate."""

    @abstractmethod
    async def delete(self, candidate_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> bool:
        """Remove a proactive candidate."""

    @abstractmethod
    def sweep_expired(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> int:
        """
        Synchronously sweep expired candidates for the given user.
        Invoked strictly on-demand; no background sweeper exists.
        """

    @abstractmethod
    def record_created_quota(self, user_id: str, created_at: datetime) -> None:
        """Record candidate creation timestamp for rolling 1-hour rate limiting."""

    @abstractmethod
    def count_created_in_window(self, user_id: str, window_seconds: int = 3600) -> int:
        """Count unique candidates created for this user within the rolling window."""


class InMemoryProactiveStore(BaseProactiveStore):
    """
    Ephemeral, user-scoped in-memory storage for proactive candidates.
    No SQLite persistence. State is held strictly in memory.
    """

    def __init__(self) -> None:
        # user_id -> { candidate_id -> ProactiveCandidate }
        self._store: Dict[str, Dict[str, ProactiveCandidate]] = {}
        # user_id -> list of creation timestamps for rate limiting
        self._created_history: Dict[str, List[datetime]] = {}

    async def add(self, candidate: ProactiveCandidate) -> ProactiveCandidate:
        user_id = candidate.user_id
        if user_id not in self._store:
            self._store[user_id] = {}
        self._store[user_id][candidate.candidate_id] = candidate
        return candidate

    async def get(self, candidate_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> Optional[ProactiveCandidate]:
        candidate = self._store.get(user_id, {}).get(candidate_id)
        if candidate is None:
            return None

        # Lazy expiration check on retrieval
        if candidate.is_expired and candidate.status != ProactiveStatus.EXPIRED:
            candidate.status = ProactiveStatus.EXPIRED
            self._store[user_id][candidate_id] = candidate

        return candidate

    async def list(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        status: Optional[ProactiveStatus] = None,
        include_expired: bool = False,
    ) -> List[ProactiveCandidate]:
        user_items = self._store.get(user_id, {})
        now = datetime.now(timezone.utc)

        results: List[ProactiveCandidate] = []
        for cand in user_items.values():
            # Lazy expiration detection
            if cand.is_expired and cand.status != ProactiveStatus.EXPIRED:
                cand.status = ProactiveStatus.EXPIRED

            if not include_expired and cand.status == ProactiveStatus.EXPIRED:
                continue

            if status is not None and cand.status != status:
                continue

            results.append(cand)

        return results

    async def update(self, candidate: ProactiveCandidate) -> ProactiveCandidate:
        user_id = candidate.user_id
        if user_id not in self._store:
            self._store[user_id] = {}
        self._store[user_id][candidate.candidate_id] = candidate
        return candidate

    async def delete(self, candidate_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> bool:
        if user_id in self._store and candidate_id in self._store[user_id]:
            del self._store[user_id][candidate_id]
            return True
        return False

    def sweep_expired(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> int:
        """
        On-demand cleanup: removes all candidates past expires_at for the user.
        No background timers or daemon processes call this.
        """
        user_items = self._store.get(user_id, {})
        if not user_items:
            return 0

        expired_ids = [cid for cid, cand in user_items.items() if cand.is_expired]
        for cid in expired_ids:
            del user_items[cid]

        if expired_ids:
            logger.debug("On-demand swept %d expired candidate(s) for user '%s'.", len(expired_ids), user_id)
        return len(expired_ids)

    def record_created_quota(self, user_id: str, created_at: datetime) -> None:
        """Record candidate creation timestamp for rolling window tracking."""
        history = self._created_history.setdefault(user_id, [])
        history.append(created_at)

    def count_created_in_window(self, user_id: str, window_seconds: int = 3600) -> int:
        """Count how many candidates were created within the rolling window."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        history = self._created_history.get(user_id, [])
        # Filter and prune timestamps older than 2x window
        active = [ts for ts in history if ts >= cutoff]
        self._created_history[user_id] = active
        return len(active)
