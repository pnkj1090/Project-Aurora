"""
Phase 5L.2 — Candidate Resolution Policy and Resolution Service.

Core Invariants:
1. MemoryCandidate != MemoryItem.
2. CandidateResolutionPolicy is 100% deterministic and server-controlled; LLM never decides persistence.
3. Every candidate resolves to exactly one of: AUTO_ACCEPT, REQUEST_USER_REVIEW, REJECT.
4. MemoryManager is the sole persistence authority; direct SQLite access is forbidden.
5. Inferred candidates are NEVER automatically persisted.
6. Sensitive candidates are NEVER automatically persisted.
7. Commitments, tasks, and episodic memories are NEVER automatically persisted.
8. Ephemeral review queue: Candidates in REQUEST_USER_REVIEW live in InMemoryCandidateReviewStore.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import List, Optional, Tuple
from pydantic import BaseModel, Field

from app.services.memory.candidates.models import (
    CandidateEvidenceType,
    CandidateSensitivity,
    MemoryCandidate,
)
from app.services.memory.candidates.store import (
    BaseCandidateReviewStore,
    ReviewableCandidate,
    get_candidate_review_store,
)
from app.services.memory.manager import MemoryManager
from app.services.memory.schemas import MemoryItem, MemorySource, MemoryStatus, MemoryType

logger = logging.getLogger("aurora.memory.candidates.resolution")


class CandidateResolutionDecision(str, Enum):
    """Authoritative outcome of candidate resolution policy evaluation."""
    AUTO_ACCEPT = "auto_accept"
    REQUEST_USER_REVIEW = "request_user_review"
    REJECT = "reject"


class CandidateResolutionResult(BaseModel):
    """Structured internal outcome of resolving a MemoryCandidate."""
    candidate_id: str
    decision: CandidateResolutionDecision
    reason: str
    memory_id: Optional[str] = None
    conflicts_with_memory_id: Optional[str] = None


def derive_temporal_expiration(
    candidate: MemoryCandidate, now: Optional[datetime] = None
) -> Optional[datetime]:
    """
    Deterministic derivation of UTC expiration from Phase 5L.1 TemporalAnchor.
    Does NOT invent arbitrary NLP dates; strictly uses recognized timeframes.
    Supports injecting `now` for deterministic testing.
    """
    if not candidate.temporal:
        return None

    tf = (candidate.temporal.timeframe or "").strip().lower()
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)

    if tf == "today":
        return now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif tf in ("this_week", "this week"):
        days_until_sunday = 6 - now.weekday()
        sunday = now + timedelta(days=days_until_sunday)
        return sunday.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif tf in ("this_month", "this month"):
        if now.month == 12:
            next_month = now.replace(
                year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
        else:
            next_month = now.replace(
                month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
        return next_month - timedelta(microseconds=1)

    return None


class CandidateResolutionPolicy:
    """
    Authoritative 9-step deterministic evaluator governing candidate persistence eligibility.
    Completely decoupled from LLM inference; enforces strict server-controlled gates.
    """

    def evaluate(
        self,
        candidate: MemoryCandidate,
        exact_duplicate: Optional[MemoryItem] = None,
        conflicting_item: Optional[MemoryItem] = None,
    ) -> Tuple[CandidateResolutionDecision, str]:
        """
        Evaluates a validated candidate through the strict 9-step precedence pipeline.
        Returns:
            (decision, reason)
        """
        content = candidate.content.strip() if candidate.content else ""

        # Step 1: Structural & Bounds Check
        if len(content) < 5 or len(content) > 500:
            return CandidateResolutionDecision.REJECT, "structural_bounds_violation"

        if candidate.confidence < 0.0 or candidate.confidence > 1.0:
            return CandidateResolutionDecision.REJECT, "invalid_confidence_range"

        if candidate.importance < 1 or candidate.importance > 5:
            return CandidateResolutionDecision.REJECT, "invalid_importance_range"

        # Step 2: Hypothetical Filter
        if candidate.temporal and candidate.temporal.is_hypothetical:
            return CandidateResolutionDecision.REJECT, "hypothetical_statement"

        # Step 3: Unsupported Evidence / Memory Type Combinations
        if candidate.evidence_type == CandidateEvidenceType.MODEL_INFERRED:
            if candidate.memory_type == MemoryType.EXPLICIT:
                return CandidateResolutionDecision.REJECT, "explicit_cannot_be_inferred"
            if candidate.memory_type == MemoryType.COMMITMENT:
                return CandidateResolutionDecision.REJECT, "commitment_cannot_be_inferred"
            if candidate.memory_type == MemoryType.TASK:
                return CandidateResolutionDecision.REJECT, "task_cannot_be_inferred"
            if candidate.memory_type == MemoryType.EPISODIC:
                return CandidateResolutionDecision.REJECT, "episodic_cannot_be_inferred"

        if candidate.memory_type == MemoryType.EPISODIC:
            if not candidate.temporal or not candidate.temporal.timeframe:
                return CandidateResolutionDecision.REJECT, "episodic_missing_temporal_anchor"

        # Step 4: Sensitivity Restrictions
        if candidate.sensitivity == CandidateSensitivity.SENSITIVE:
            if candidate.evidence_type == CandidateEvidenceType.MODEL_INFERRED:
                return CandidateResolutionDecision.REJECT, "sensitive_inferred_prohibited"
            # Explicit sensitive
            if candidate.confidence >= 0.75:
                return CandidateResolutionDecision.REQUEST_USER_REVIEW, "sensitive_explicit_requires_review"
            return CandidateResolutionDecision.REJECT, "sensitive_explicit_low_confidence"

        # Step 5: Universal Confidence Floor
        if candidate.confidence < 0.70:
            return CandidateResolutionDecision.REJECT, "below_universal_confidence_floor"

        # Step 6: Exact Duplicate Handling
        if exact_duplicate is not None:
            # Explicit permanent candidates meeting auto-accept confidence thresholds
            # are allowed to AUTO_ACCEPT so MemoryManager merges tags natively.
            is_temp = candidate.temporal and candidate.temporal.is_temporary
            if (
                candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT
                and not is_temp
                and (
                    (candidate.memory_type == MemoryType.PREFERENCE and candidate.confidence >= 0.85)
                    or (candidate.memory_type == MemoryType.FACT and candidate.confidence >= 0.90)
                    or (candidate.memory_type == MemoryType.EXPLICIT and candidate.confidence >= 0.90)
                )
            ):
                return CandidateResolutionDecision.AUTO_ACCEPT, "exact_duplicate_merge"

            # Inferred or review-bound duplicates are rejected to avoid queue pollution
            return CandidateResolutionDecision.REJECT, "already_exists"

        # Step 7: Potential Conflict Detection
        if conflicting_item is not None:
            if candidate.confidence >= 0.75:
                return CandidateResolutionDecision.REQUEST_USER_REVIEW, "potential_conflict_requires_review"
            return CandidateResolutionDecision.REJECT, "low_confidence_conflict"

        # Step 8: Memory-Type & Evidence-Specific Evaluation
        is_temporary = bool(candidate.temporal and candidate.temporal.is_temporary)
        c = candidate.confidence

        if candidate.memory_type == MemoryType.COMMITMENT:
            if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT and c >= 0.80:
                return CandidateResolutionDecision.REQUEST_USER_REVIEW, "commitment_requires_review"
            return CandidateResolutionDecision.REJECT, "commitment_low_confidence"

        if candidate.memory_type == MemoryType.TASK:
            if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT and c >= 0.80:
                return CandidateResolutionDecision.REQUEST_USER_REVIEW, "task_requires_review"
            return CandidateResolutionDecision.REJECT, "task_low_confidence"

        if candidate.memory_type == MemoryType.EPISODIC:
            if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT and c >= 0.80:
                return CandidateResolutionDecision.REQUEST_USER_REVIEW, "episodic_requires_review"
            return CandidateResolutionDecision.REJECT, "episodic_low_confidence"

        if candidate.memory_type == MemoryType.PREFERENCE:
            if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT:
                if is_temporary:
                    return CandidateResolutionDecision.REQUEST_USER_REVIEW, "temporary_preference_requires_review"
                else:
                    if c >= 0.85:
                        return CandidateResolutionDecision.AUTO_ACCEPT, "explicit_preference_auto_accept"
                    if c >= 0.70:
                        return CandidateResolutionDecision.REQUEST_USER_REVIEW, "explicit_preference_moderate_confidence"
                    return CandidateResolutionDecision.REJECT, "preference_low_confidence"
            elif candidate.evidence_type == CandidateEvidenceType.MODEL_INFERRED:
                if c >= 0.75:
                    return CandidateResolutionDecision.REQUEST_USER_REVIEW, "inferred_preference_requires_review"
                return CandidateResolutionDecision.REJECT, "inferred_preference_low_confidence"

        if candidate.memory_type == MemoryType.FACT:
            if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT:
                if is_temporary:
                    return CandidateResolutionDecision.REQUEST_USER_REVIEW, "temporary_fact_requires_review"
                else:
                    if c >= 0.90:
                        return CandidateResolutionDecision.AUTO_ACCEPT, "explicit_fact_auto_accept"
                    if c >= 0.70:
                        return CandidateResolutionDecision.REQUEST_USER_REVIEW, "explicit_fact_moderate_confidence"
                    return CandidateResolutionDecision.REJECT, "fact_low_confidence"
            elif candidate.evidence_type == CandidateEvidenceType.MODEL_INFERRED:
                if c >= 0.80:
                    return CandidateResolutionDecision.REQUEST_USER_REVIEW, "inferred_fact_requires_review"
                return CandidateResolutionDecision.REJECT, "inferred_fact_low_confidence"

        if candidate.memory_type == MemoryType.EXPLICIT:
            if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT:
                if c >= 0.90:
                    return CandidateResolutionDecision.AUTO_ACCEPT, "explicit_command_auto_accept"
                if c >= 0.75:
                    return CandidateResolutionDecision.REQUEST_USER_REVIEW, "explicit_command_moderate_confidence"
                return CandidateResolutionDecision.REJECT, "explicit_command_low_confidence"

        # Step 9: Final Fallback
        return CandidateResolutionDecision.REJECT, "unhandled_policy_case"


class CandidateResolutionService:
    """
    Orchestrates policy evaluation, downstream MemoryManager persistence for AUTO_ACCEPT,
    and InMemoryCandidateReviewStore enqueueing for REQUEST_USER_REVIEW.
    """

    def __init__(
        self,
        memory_manager: MemoryManager,
        review_store: Optional[BaseCandidateReviewStore] = None,
        policy: Optional[CandidateResolutionPolicy] = None,
    ):
        self.memory_manager = memory_manager
        self.review_store = review_store or get_candidate_review_store()
        self.policy = policy or CandidateResolutionPolicy()

    async def resolve_candidates(
        self,
        candidates: List[MemoryCandidate],
        active_user_id: str,
    ) -> List[CandidateResolutionResult]:
        """
        Evaluates a batch of candidates for active user.
        Executes AUTO_ACCEPT via MemoryManager, adds REQUEST_USER_REVIEW to store,
        and discards REJECT items.
        """
        results: List[CandidateResolutionResult] = []

        for candidate in candidates:
            try:
                # Enforce server-controlled identity invariant
                if candidate.user_id != active_user_id:
                    logger.warning(
                        "Candidate ownership mismatch: candidate user %s != active user %s",
                        candidate.user_id,
                        active_user_id,
                    )
                    continue

                # Query MemoryManager for read-only duplicate and potential conflict information
                exact_dup, conflict_item = await self.memory_manager.find_duplicate_or_conflict(
                    content=candidate.content,
                    type=candidate.memory_type,
                    tags=candidate.tags,
                    user_id=active_user_id,
                )

                decision, reason = self.policy.evaluate(
                    candidate=candidate,
                    exact_duplicate=exact_dup,
                    conflicting_item=conflict_item,
                )

                conflicts_with_id = conflict_item.id if conflict_item else None

                if decision == CandidateResolutionDecision.AUTO_ACCEPT:
                    source = (
                        MemorySource.USER_EXPLICIT
                        if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT
                        else MemorySource.INFERRED
                    )
                    expires_at = derive_temporal_expiration(candidate)

                    metadata = {
                        "candidate_id": candidate.candidate_id,
                        "conversation_id": candidate.conversation_id,
                        "turn_index": candidate.turn_index,
                        "extractor_version": candidate.extractor_version,
                        "resolution": "auto_accepted",
                        "resolved_at": datetime.now(timezone.utc).isoformat(),
                        "temporal": candidate.temporal.model_dump() if candidate.temporal else None,
                        "user_quote": candidate.user_quote,
                    }
                    if candidate.sensitivity == CandidateSensitivity.SENSITIVE:
                        metadata["sensitivity"] = "sensitive"

                    saved, _ = await self.memory_manager.create_memory(
                        content=candidate.content,
                        type=candidate.memory_type,
                        importance=candidate.importance,
                        confidence=candidate.confidence,
                        source=source,
                        status=MemoryStatus.ACTIVE,
                        tags=candidate.tags,
                        expires_at=expires_at,
                        metadata=metadata,
                        user_id=active_user_id,
                    )
                    results.append(
                        CandidateResolutionResult(
                            candidate_id=candidate.candidate_id,
                            decision=decision,
                            reason=reason,
                            memory_id=saved.id,
                            conflicts_with_memory_id=conflicts_with_id,
                        )
                    )

                elif decision == CandidateResolutionDecision.REQUEST_USER_REVIEW:
                    review_item = ReviewableCandidate(
                        candidate=candidate,
                        conflicts_with_memory_id=conflicts_with_id,
                        existing_conflicting_content=conflict_item.content if conflict_item else None,
                    )
                    await self.review_store.add(review_item)
                    results.append(
                        CandidateResolutionResult(
                            candidate_id=candidate.candidate_id,
                            decision=decision,
                            reason=reason,
                            conflicts_with_memory_id=conflicts_with_id,
                        )
                    )

                else:
                    # REJECT: Log safe diagnostic metric without user text or sensitive content
                    logger.debug(
                        "Candidate rejected: candidate_id=%s reason=%s",
                        candidate.candidate_id,
                        reason,
                    )
                    results.append(
                        CandidateResolutionResult(
                            candidate_id=candidate.candidate_id,
                            decision=decision,
                            reason=reason,
                        )
                    )
            except Exception as cand_exc:
                logger.warning(
                    "Error resolving candidate %s (isolated safely): %s",
                    getattr(candidate, "candidate_id", "unknown"),
                    cand_exc,
                )
                continue

        return results
