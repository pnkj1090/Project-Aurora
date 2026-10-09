"""
Phase 5H — ProactivePolicy: Deterministic evaluation of proactive candidates.

Evaluation Pipeline (Strict Order):
1. Input validation & sanitization
2. Server identity and timestamps
3. Expiration check
4. Deterministic deduplication (duplicates do NOT consume quota)
5. Per-user rate limiting (5 unique candidates per rolling 1 hour)
6. Permission eligibility (CALENDAR, LOCATION, NOTIFICATIONS)
7. Final status assignment (ELIGIBLE or SUPPRESSED or EXPIRED)

Invariants:
- Deduplication occurs BEFORE rate limiting.
- Deduplication history: PENDING, ELIGIBLE, DELIVERED block duplicates.
  DISMISSED and CANCELLED also block duplicates while their original TTL is unexpired.
  EXPIRED candidates do not block duplicates.
- Permission authority belongs strictly to PermissionManager. The LLM cannot grant or alter permissions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from app.services.permissions import (
    PermissionManager,
    PermissionType,
    default_permission_manager,
)
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveStatus,
    ProactiveTrigger,
    ProactiveAction,
    MAX_PROACTIVE_CANDIDATES_PER_USER_PER_HOUR,
)
from app.services.proactive.store import BaseProactiveStore

logger = logging.getLogger("aurora.proactive.policy")

# ── Permission Mappings (Server-Controlled) ───────────────────────────────────

TRIGGER_PERMISSION_MAP: Dict[ProactiveTrigger, PermissionType] = {
    ProactiveTrigger.CALENDAR_EVENT: PermissionType.CALENDAR,
    ProactiveTrigger.LOCATION_CONTEXT: PermissionType.LOCATION,
}

SOURCE_PERMISSION_MAP: Dict[str, PermissionType] = {
    "calendar": PermissionType.CALENDAR,
    "location": PermissionType.LOCATION,
    "reminders": PermissionType.REMINDERS,
}

ACTION_PERMISSION_MAP: Dict[ProactiveAction, PermissionType] = {
    ProactiveAction.NOTIFY: PermissionType.NOTIFICATIONS,
    ProactiveAction.OPEN_REMINDER: PermissionType.REMINDERS,
    ProactiveAction.CANCEL_REMINDER: PermissionType.REMINDERS,
    ProactiveAction.OPEN_CALENDAR_EVENT: PermissionType.CALENDAR,
}


class ProactivePolicy:
    """
    Deterministic policy evaluator for proactive proposals.
    """

    def __init__(self, permission_manager: Optional[PermissionManager] = None) -> None:
        self._permission_manager = permission_manager

    @property
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager or default_permission_manager

    async def evaluate(
        self,
        candidate: ProactiveCandidate,
        store: BaseProactiveStore,
    ) -> ProactiveCandidate:
        """
        Evaluate a candidate through the strict 7-step pipeline.
        Returns the candidate with its final status and suppression_reason.
        """
        user_id = candidate.user_id

        # Step 1: Input validation (handled by Pydantic model construction)

        # Step 2: Server identity and timestamps
        # Ensures candidate has valid timezone-aware UTC created_at and expires_at
        now = datetime.now(timezone.utc)
        if candidate.created_at.tzinfo is None or candidate.expires_at.tzinfo is None:
            raise ValueError("Candidate timestamps must be timezone-aware (UTC).")

        # Step 3: Expiration check
        if candidate.is_expired:
            candidate.status = ProactiveStatus.EXPIRED
            candidate.suppression_reason = "expired"
            logger.debug("Candidate '%s' is expired at evaluation.", candidate.candidate_id)
            return candidate

        # Step 4: Deterministic deduplication
        # Checks existing candidates for matching dedup_key.
        # Deduplication occurs BEFORE rate limiting.
        existing_candidates = await store.list(user_id=user_id, include_expired=True)
        is_duplicate = False

        for existing in existing_candidates:
            if existing.candidate_id == candidate.candidate_id:
                continue

            # Check matching deduplication key
            if existing.dedup_key == candidate.dedup_key:
                # If existing is active (PENDING, ELIGIBLE, DELIVERED, ACCEPTED), it blocks duplicates
                if existing.status in (
                    ProactiveStatus.PENDING,
                    ProactiveStatus.ELIGIBLE,
                    ProactiveStatus.DELIVERED,
                    ProactiveStatus.ACCEPTED,
                ):
                    is_duplicate = True
                    break

                # If existing is terminal (DISMISSED, CANCELLED, EXECUTED, FAILED), it blocks duplicates
                # WHILE within its original TTL (unexpired)
                if existing.status in (
                    ProactiveStatus.DISMISSED,
                    ProactiveStatus.CANCELLED,
                    ProactiveStatus.EXECUTED,
                    ProactiveStatus.FAILED,
                ):
                    if not existing.is_expired:
                        is_duplicate = True
                        break

                # Note: EXPIRED candidates do NOT block duplicates

        if is_duplicate:
            candidate.status = ProactiveStatus.SUPPRESSED
            candidate.suppression_reason = "deduplicated"
            logger.info(
                "Candidate '%s' for user '%s' suppressed: duplicate proposal exists.",
                candidate.candidate_id,
                user_id,
            )
            # Duplicate does NOT consume quota
            return candidate

        # Step 5: Per-user rate limiting (rolling 1-hour window)
        # Any unique candidate reaching this step consumes quota even if later suppressed by permission.
        quota_used = store.count_created_in_window(user_id, window_seconds=3600)
        rate_limited = quota_used >= MAX_PROACTIVE_CANDIDATES_PER_USER_PER_HOUR

        # Record quota consumption for this unique candidate
        store.record_created_quota(user_id, candidate.created_at)

        if rate_limited:
            candidate.status = ProactiveStatus.SUPPRESSED
            candidate.suppression_reason = "rate_limit_exceeded"
            logger.info(
                "Candidate '%s' for user '%s' suppressed: rate limit (%d/hour) exceeded.",
                candidate.candidate_id,
                user_id,
                MAX_PROACTIVE_CANDIDATES_PER_USER_PER_HOUR,
            )
            return candidate

        # Step 6: Permission eligibility
        required_permissions: Set[PermissionType] = set()

        if candidate.trigger in TRIGGER_PERMISSION_MAP:
            required_permissions.add(TRIGGER_PERMISSION_MAP[candidate.trigger])

        if candidate.source in SOURCE_PERMISSION_MAP:
            required_permissions.add(SOURCE_PERMISSION_MAP[candidate.source])

        if candidate.action in ACTION_PERMISSION_MAP:
            required_permissions.add(ACTION_PERMISSION_MAP[candidate.action])

        for perm in required_permissions:
            is_auth = await self.permission_manager.is_authorized(perm, user_id=user_id)
            if not is_auth:
                candidate.status = ProactiveStatus.SUPPRESSED
                candidate.suppression_reason = "permission_unauthorized"
                logger.info(
                    "Candidate '%s' for user '%s' suppressed: required permission '%s' not authorized.",
                    candidate.candidate_id,
                    user_id,
                    perm.value,
                )
                return candidate

        # Step 7: Final status assignment
        candidate.status = ProactiveStatus.ELIGIBLE
        candidate.suppression_reason = None
        logger.info(
            "Candidate '%s' for user '%s' evaluated as ELIGIBLE.",
            candidate.candidate_id,
            user_id,
        )
        return candidate
