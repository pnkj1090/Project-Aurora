"""
Phase 5H — ProactiveManager: Lifecycle, ownership, and policy coordination.

Core Responsibilities:
- Candidate validation and server-controlled creation
- Policy evaluation and deduplication coordination
- On-demand / lazy expiration sweeping (no background sweepers)
- Deterministic pagination and ordering
- User ownership and strict isolation
- Dismissal and cancellation handling
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions import (
    PermissionManager,
    default_permission_manager,
)
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveStatus,
    ProactiveTrigger,
    ProactiveAction,
    ProactivePriority,
    DEFAULT_PROACTIVE_TTL_SECONDS,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
)
from app.services.proactive.store import (
    BaseProactiveStore,
    InMemoryProactiveStore,
)
from app.services.proactive.policy import ProactivePolicy

logger = logging.getLogger("aurora.proactive.manager")


class ProactiveManager:
    """
    Manager coordinating proactive candidate lifecycle, policy evaluation,
    and storage.
    """

    def __init__(
        self,
        store: Optional[BaseProactiveStore] = None,
        policy: Optional[ProactivePolicy] = None,
        permission_manager: Optional[PermissionManager] = None,
    ) -> None:
        self.store = store or InMemoryProactiveStore()
        self.policy = policy or ProactivePolicy(permission_manager=permission_manager)

    # ── Candidate Creation ───────────────────────────────────────────────────

    async def create_candidate(
        self,
        trigger: ProactiveTrigger,
        title: str,
        message: str,
        source: str,
        action: ProactiveAction,
        priority: ProactivePriority = ProactivePriority.MEDIUM,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        reference_id: Optional[str] = None,
        action_payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ProactiveCandidate:
        """
        Create and evaluate a new proactive candidate.
        1. On-demand sweep of expired candidates.
        2. Construct candidate with server-generated ID and timestamps.
        3. Evaluate through deterministic policy.
        4. Store and return.
        """
        # Step 1: On-demand sweep of expired candidates for this user
        self.store.sweep_expired(user_id=user_id)

        # Step 2: Construct candidate (server generates timestamps and TTL)
        now = datetime.now(timezone.utc)
        candidate = ProactiveCandidate(
            user_id=user_id,
            trigger=trigger,
            title=title,
            message=message,
            priority=priority,
            source=source,
            action=action,
            created_at=now,
            expires_at=now + timedelta(seconds=DEFAULT_PROACTIVE_TTL_SECONDS),
            reference_id=reference_id,
            action_payload=action_payload or {},
            metadata=metadata or {},
        )

        # Step 3: Evaluate through policy pipeline (dedup -> rate limit -> permissions)
        evaluated = await self.policy.evaluate(candidate, self.store)

        # Step 4: Store
        await self.store.add(evaluated)
        return evaluated

    async def run_subsystem_evaluation(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        evaluators: Optional[List[Any]] = None,
    ) -> List[ProactiveCandidate]:
        """
        On-demand domain evaluation invoked explicitly (e.g. POST /evaluate or pull-to-refresh).
        Every candidate passes through the Phase 5H policy pipeline (dedup, rate limit, permissions).
        Evaluators cannot write directly to the proactive store.
        """
        if evaluators is None:
            from app.services.proactive.evaluators import (
                TaskProposalEvaluator,
                CommitmentProposalEvaluator,
                CalendarProposalEvaluator,
                ReminderProposalEvaluator,
                ScheduleConflictEvaluator,
            )
            evaluators = [
                TaskProposalEvaluator(),
                CommitmentProposalEvaluator(),
                CalendarProposalEvaluator(),
                ReminderProposalEvaluator(),
                ScheduleConflictEvaluator(),
            ]

        generated: List[ProactiveCandidate] = []
        for evaluator in evaluators:
            try:
                raw_proposals = await evaluator.evaluate_proposals(user_id=user_id)
                for raw in raw_proposals:
                    evaluated = await self.create_candidate(
                        trigger=raw.trigger,
                        title=raw.title,
                        message=raw.message,
                        source=raw.source,
                        action=raw.action,
                        priority=raw.priority,
                        user_id=user_id,
                        reference_id=raw.reference_id,
                        action_payload=raw.action_payload,
                        metadata=raw.metadata,
                    )
                    generated.append(evaluated)
            except Exception as exc:
                logger.warning("Error running subsystem evaluator '%s': %s", type(evaluator).__name__, exc)

        return generated

    # ── Retrieval & Listing ──────────────────────────────────────────────────

    async def get_candidate(
        self,
        candidate_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[ProactiveCandidate]:
        """
        Retrieve a single candidate by ID with lazy expiration transition.
        Returns None if not found or belonging to another user.
        """
        return await self.store.get(candidate_id, user_id=user_id)

    async def list_candidates(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        status: Optional[ProactiveStatus] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
        include_expired: bool = False,
    ) -> Tuple[List[ProactiveCandidate], int]:
        """
        List candidates for a user with on-demand sweep and deterministic ordering.
        Ordering:
        1. Priority descending (HIGH > MEDIUM > LOW)
        2. created_at descending (newest first)
        3. candidate_id ascending (tie-breaker)
        Returns: (paginated_candidates, total_matching)
        """
        # On-demand lazy sweep before listing
        self.store.sweep_expired(user_id=user_id)

        all_candidates = await self.store.list(
            user_id=user_id,
            status=status,
            include_expired=include_expired,
        )

        # Deterministic sorting
        # Priority weight descending, created_at descending, candidate_id ascending
        all_candidates.sort(
            key=lambda c: (
                -c.priority.weight,
                -c.created_at.timestamp(),
                c.candidate_id,
            )
        )

        total = len(all_candidates)
        paginated = all_candidates[offset : offset + limit]
        return paginated, total

    # ── Candidate Re-Evaluation ──────────────────────────────────────────────

    async def evaluate_candidate(
        self,
        candidate_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[ProactiveCandidate]:
        """
        Re-evaluate an existing candidate (e.g. if permissions changed).
        """
        candidate = await self.get_candidate(candidate_id, user_id=user_id)
        if candidate is None:
            return None

        # Re-evaluate
        evaluated = await self.policy.evaluate(candidate, self.store)
        await self.store.update(evaluated)
        return evaluated

    # ── Lifecycle Actions ────────────────────────────────────────────────────

    async def dismiss_candidate(
        self,
        candidate_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[ProactiveCandidate]:
        """
        Explicit user dismissal of a proposal.
        Transitions candidate to DISMISSED status.
        """
        candidate = await self.get_candidate(candidate_id, user_id=user_id)
        if candidate is None:
            return None

        candidate.status = ProactiveStatus.DISMISSED
        await self.store.update(candidate)
        logger.info("Candidate '%s' dismissed for user '%s'.", candidate_id, user_id)
        return candidate

    async def cancel_candidate(
        self,
        candidate_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[ProactiveCandidate]:
        """
        Explicit cancellation of a proposal before delivery.
        Transitions candidate to CANCELLED status.
        """
        candidate = await self.get_candidate(candidate_id, user_id=user_id)
        if candidate is None:
            return None

        candidate.status = ProactiveStatus.CANCELLED
        await self.store.update(candidate)
        logger.info("Candidate '%s' cancelled for user '%s'.", candidate_id, user_id)
        return candidate

    # ── On-Demand Sweeping ───────────────────────────────────────────────────

    def sweep_expired(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> int:
        """
        On-demand cleanup: removes all candidates past expires_at for the user.
        No background timers or daemon processes call this.
        """
        return self.store.sweep_expired(user_id=user_id)


# ── Dependency Provider / Singleton ──────────────────────────────────────────

_default_proactive_manager: Optional[ProactiveManager] = None


def get_proactive_manager() -> ProactiveManager:
    """Retrieve or create the singleton ProactiveManager."""
    global _default_proactive_manager
    if _default_proactive_manager is None:
        _default_proactive_manager = ProactiveManager()
    return _default_proactive_manager


def get_proactive_manager_dependency() -> ProactiveManager:
    """FastAPI dependency provider for ProactiveManager."""
    return get_proactive_manager()
