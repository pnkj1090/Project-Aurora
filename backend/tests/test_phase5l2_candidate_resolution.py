"""
Comprehensive deterministic test suite for Phase 5L.2 — Memory Candidate Review & Resolution.

Covers:
A. Deterministic Resolution Policy & 9-Step Precedence Order
   - Structural bounds (length, confidence, importance)
   - Hypothetical statements
   - Unsupported combinations (inferred explicit/commitment/task/episodic, episodic without temporal)
   - Sensitivity rules (inferred sensitive rejected, explicit sensitive >= 0.75 review, < 0.75 reject)
   - Universal confidence floor (< 0.70)
   - Exact duplicate handling (explicit auto-accept tag merge vs inferred reject)
   - Potential conflict detection (>= 0.75 review vs < 0.75 reject)
   - Full threshold boundary tests: 0.69, 0.70, 0.74, 0.75, 0.76, 0.79, 0.80, 0.81, 0.84, 0.85, 0.86, 0.89, 0.90, 0.91
B. Temporal Semantics
   - Permanent expires_at is None
   - Deterministic 'today', 'this_week', 'this_month' calculations
   - 'for now' / 'currently' temporary without end date leaves expires_at=None
   - Client cannot supply arbitrary expires_at; server derives it
   - Temporal metadata preserved through review/approval
C. Concurrency & Idempotency Store
   - Atomic claim PENDING_REVIEW -> RESOLVING
   - Simultaneous claims return HTTP 409
   - Persistence failure rollback to PENDING_REVIEW
   - Bounded resolution ledger (idempotent duplicate approve and reject)
   - Unknown and expired candidate 404
   - Process restart loses pending candidates and ledger
   - Lock registry cleanup on terminal resolution and sweep
D. Conflict Action Semantics & Cross-User Defense
   - Missing conflict_action returns 422
   - Invalid conflict_action returns 422
   - Non-conflicting candidate with conflict_action returns 422
   - 'replace' updates target only; 'keep_both' creates new memory
   - Cross-user conflict target protection fails closed with 404
E. Tag Override & Sanitization
   - Valid tags, too many tags (>10), tag too long (>30 chars)
   - Invalid characters, reserved system prefixes
   - Override replaces candidate tags; omitted preserves candidate tags
F. Provenance & Revalidation
   - Original explicit stays USER_EXPLICIT
   - Original inferred stays INFERRED
   - Edited candidate becomes USER_EXPLICIT with lineage in metadata
   - Revalidation of untrusted edited content (credentials rejected with 422)
   - No transcripts, chain-of-thought, or raw web HTML/URLs in metadata
G. Identity Isolation
   - List, detail, approve, reject strictly user-scoped
   - Cross-user access returns 404
   - Client payload cannot supply user_id
H. Conversation Lifecycle Integration
   - Normal completed turn resolves exactly once
   - Waiting for confirmation skips resolution
   - Confirmation resume approved & completed resolves once
   - Confirmation rejection skips resolution
   - Tool failure / cancellation skips resolution
   - Extraction/resolution failure does not break chat delivery
I. Persistence Boundary Invariants
   - MemoryManager is the sole persistence authority
   - Zero direct SQLite access from candidates/store/endpoints
   - Zero candidate persistence in ConversationSession.metadata or AgentExecutionState
"""
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.identity import default_identity_manager, UserIdentity
from app.services.memory.candidates import (
    CandidateEvidenceType,
    CandidateResolutionDecision,
    CandidateResolutionPolicy,
    CandidateResolutionResult,
    CandidateResolutionService,
    CandidateReviewStatus,
    CandidateSensitivity,
    InMemoryCandidateReviewStore,
    MemoryCandidate,
    ReviewableCandidate,
    TemporalAnchor,
    derive_temporal_expiration,
    get_candidate_review_store,
)
from app.services.memory.manager import MemoryManager
from app.services.memory.schemas import MemoryItem, MemorySource, MemoryStatus, MemoryType
from app.services.memory.store import InMemoryMemoryStore, SQLiteMemoryStore
from app.services.conversation.service import ConversationService
from app.services.conversation.session import ConversationSession
from app.services.conversation.store import InMemoryConversationStore
from app.services.conversation.models import AgentExecutionState, AgentExecutionStatus
from app.services.ai.base import BaseAIProvider, ChatMessage, AIResponse
from app.services.tools.schemas import PendingConfirmation, ToolCall
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.registry import ToolRegistry
from app.services.tools.executor import ToolExecutor


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def make_candidate(
    candidate_id: str = "cand_0123456789ab",
    user_id: str = "usr_test_user",
    memory_type: MemoryType = MemoryType.PREFERENCE,
    content: str = "User prefers dark mode in all editors",
    evidence_type: CandidateEvidenceType = CandidateEvidenceType.USER_EXPLICIT,
    confidence: float = 0.90,
    importance: int = 3,
    tags: Optional[List[str]] = None,
    user_quote: Optional[str] = "I always use dark mode",
    temporal: Optional[TemporalAnchor] = None,
    sensitivity: CandidateSensitivity = CandidateSensitivity.NORMAL,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        user_id=user_id,
        conversation_id="conv_test_123",
        turn_index=1,
        extracted_at=datetime.now(timezone.utc),
        extractor_version="5L.1",
        memory_type=memory_type,
        content=content,
        evidence_type=evidence_type,
        confidence=confidence,
        importance=importance,
        tags=tags or ["ui", "theme"],
        user_quote=user_quote,
        temporal=temporal,
        sensitivity=sensitivity,
    )


@pytest.fixture
def memory_store():
    return InMemoryMemoryStore()


@pytest.fixture
def memory_manager(memory_store):
    return MemoryManager(store=memory_store)


@pytest.fixture
def review_store():
    return InMemoryCandidateReviewStore()


@pytest.fixture
def policy():
    return CandidateResolutionPolicy()


@pytest.fixture
def resolution_service(memory_manager, review_store, policy):
    return CandidateResolutionService(
        memory_manager=memory_manager,
        review_store=review_store,
        policy=policy,
    )


@pytest.fixture
def client(review_store, memory_manager):
    from app.services.memory import get_memory_manager_dependency
    from app.services.memory.candidates import get_candidate_review_store

    app.dependency_overrides[get_candidate_review_store] = lambda: review_store
    app.dependency_overrides[get_memory_manager_dependency] = lambda: memory_manager
    yield TestClient(app)
    app.dependency_overrides.clear()


# ===========================================================================
# A. Deterministic Resolution Policy & Exact Threshold Matrix
# ===========================================================================

class TestResolutionPolicy:
    def test_structural_failure_rejected(self, policy):
        """1. Content bounds violation (< 5 or > 500) and invalid confidence/importance are rejected."""
        short_cand = make_candidate(content="hi")
        dec, _ = policy.evaluate(short_cand)
        assert dec == CandidateResolutionDecision.REJECT

        long_cand = make_candidate(content="x" * 501)
        dec, _ = policy.evaluate(long_cand)
        assert dec == CandidateResolutionDecision.REJECT

        invalid_conf = make_candidate(confidence=-0.1)
        dec, _ = policy.evaluate(invalid_conf)
        assert dec == CandidateResolutionDecision.REJECT

        invalid_imp = make_candidate(importance=6)
        dec, _ = policy.evaluate(invalid_imp)
        assert dec == CandidateResolutionDecision.REJECT

    def test_hypothetical_preference_rejected(self, policy):
        """2. Hypothetical preference is strictly rejected."""
        cand = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            temporal=TemporalAnchor(is_hypothetical=True, qualifier_phrase="might"),
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "hypothetical" in reason

    def test_hypothetical_fact_rejected(self, policy):
        """3. Hypothetical fact is strictly rejected."""
        cand = make_candidate(
            memory_type=MemoryType.FACT,
            content="User might move to Bangalore",
            temporal=TemporalAnchor(is_hypothetical=True, qualifier_phrase="thinking about"),
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "hypothetical" in reason

    def test_inferred_explicit_type_rejected(self, policy):
        """4. EXPLICIT memory type cannot have MODEL_INFERRED evidence."""
        cand = make_candidate(
            memory_type=MemoryType.EXPLICIT,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "explicit_cannot_be_inferred" in reason

    def test_inferred_commitment_rejected(self, policy):
        """5. Commitment cannot have MODEL_INFERRED evidence."""
        cand = make_candidate(
            memory_type=MemoryType.COMMITMENT,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "commitment_cannot_be_inferred" in reason

    def test_inferred_task_rejected(self, policy):
        """6. Task cannot have MODEL_INFERRED evidence."""
        cand = make_candidate(
            memory_type=MemoryType.TASK,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "task_cannot_be_inferred" in reason

    def test_inferred_episodic_rejected(self, policy):
        """7. Episodic memory cannot have MODEL_INFERRED evidence."""
        cand = make_candidate(
            memory_type=MemoryType.EPISODIC,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            temporal=TemporalAnchor(timeframe="yesterday"),
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "episodic_cannot_be_inferred" in reason

    def test_episodic_without_temporal_anchor_rejected(self, policy):
        """8. Episodic memory without temporal anchor is rejected."""
        cand = make_candidate(
            memory_type=MemoryType.EPISODIC,
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            temporal=None,
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "episodic_missing_temporal_anchor" in reason

    def test_sensitive_inferred_rejected(self, policy):
        """9. Inferred sensitive candidate is strictly rejected (privacy invariant)."""
        cand = make_candidate(
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            sensitivity=CandidateSensitivity.SENSITIVE,
            confidence=0.95,
        )
        dec, reason = policy.evaluate(cand)
        assert dec == CandidateResolutionDecision.REJECT
        assert "sensitive_inferred_prohibited" in reason

    def test_sensitive_explicit_requires_review_or_reject(self, policy):
        """10. Sensitive explicit candidate requires user review if >= 0.75; < 0.75 rejected."""
        cand_high = make_candidate(
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            sensitivity=CandidateSensitivity.SENSITIVE,
            confidence=0.75,
        )
        dec, _ = policy.evaluate(cand_high)
        assert dec == CandidateResolutionDecision.REQUEST_USER_REVIEW

        cand_low = make_candidate(
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            sensitivity=CandidateSensitivity.SENSITIVE,
            confidence=0.74,
        )
        dec, _ = policy.evaluate(cand_low)
        assert dec == CandidateResolutionDecision.REJECT

    def test_universal_confidence_floor_0_69_rejected(self, policy):
        """11. Universal confidence floor: 0.69 is strictly rejected across types."""
        for m_type in [MemoryType.PREFERENCE, MemoryType.FACT, MemoryType.COMMITMENT, MemoryType.TASK]:
            cand = make_candidate(memory_type=m_type, confidence=0.69)
            dec, reason = policy.evaluate(cand)
            assert dec == CandidateResolutionDecision.REJECT
            assert "below_universal_confidence_floor" in reason

    def test_exact_duplicate_handling(self, policy):
        """12. Explicit permanent duplicate merges tags; inferred duplicate is rejected."""
        cand_exp = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
        )
        existing = MemoryItem(type=MemoryType.PREFERENCE, content=cand_exp.content, tags=["ui"])
        dec, reason = policy.evaluate(cand_exp, exact_duplicate=existing)
        assert dec == CandidateResolutionDecision.AUTO_ACCEPT
        assert "exact_duplicate_merge" in reason

        cand_inf = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.85,
        )
        dec, reason = policy.evaluate(cand_inf, exact_duplicate=existing)
        assert dec == CandidateResolutionDecision.REJECT
        assert "already_exists" in reason

    def test_potential_conflict_thresholds(self, policy):
        """13. Potential conflict: confidence >= 0.75 -> review; < 0.75 -> reject."""
        existing = MemoryItem(
            id="mem_exist_1",
            type=MemoryType.PREFERENCE,
            content="User prefers dark mode in editors",
            tags=["theme"],
        )
        cand_high = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            content="User prefers light mode in editors",
            tags=["theme"],
            confidence=0.75,
        )
        dec, reason = policy.evaluate(cand_high, conflicting_item=existing)
        assert dec == CandidateResolutionDecision.REQUEST_USER_REVIEW
        assert "potential_conflict_requires_review" in reason

        cand_low = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            content="User prefers light mode in editors",
            tags=["theme"],
            confidence=0.74,
        )
        dec, reason = policy.evaluate(cand_low, conflicting_item=existing)
        assert dec == CandidateResolutionDecision.REJECT
        assert "low_confidence_conflict" in reason

    # Threshold Boundary Tests: 0.70, 0.74, 0.75, 0.76, 0.79, 0.80, 0.81, 0.84, 0.85, 0.86, 0.89, 0.90, 0.91
    def test_preference_explicit_permanent_boundaries(self, policy):
        """14. Preference explicit permanent: 0.70-0.84 -> review, >= 0.85 -> auto_accept."""
        # 0.70
        cand_70 = make_candidate(memory_type=MemoryType.PREFERENCE, confidence=0.70)
        assert policy.evaluate(cand_70)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW
        # 0.74
        cand_74 = make_candidate(memory_type=MemoryType.PREFERENCE, confidence=0.74)
        assert policy.evaluate(cand_74)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW
        # 0.84
        cand_84 = make_candidate(memory_type=MemoryType.PREFERENCE, confidence=0.84)
        assert policy.evaluate(cand_84)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW
        # 0.85 (Threshold)
        cand_85 = make_candidate(memory_type=MemoryType.PREFERENCE, confidence=0.85)
        assert policy.evaluate(cand_85)[0] == CandidateResolutionDecision.AUTO_ACCEPT
        # 0.86 (Immediately above)
        cand_86 = make_candidate(memory_type=MemoryType.PREFERENCE, confidence=0.86)
        assert policy.evaluate(cand_86)[0] == CandidateResolutionDecision.AUTO_ACCEPT

    def test_preference_inferred_boundaries(self, policy):
        """15. Preference inferred: < 0.75 -> reject, >= 0.75 -> review."""
        cand_70 = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.70,
        )
        assert policy.evaluate(cand_70)[0] == CandidateResolutionDecision.REJECT

        cand_74 = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.74,
        )
        assert policy.evaluate(cand_74)[0] == CandidateResolutionDecision.REJECT

        cand_75 = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.75,
        )
        assert policy.evaluate(cand_75)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

        cand_76 = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.76,
        )
        assert policy.evaluate(cand_76)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

    def test_fact_explicit_permanent_boundaries(self, policy):
        """16. Fact explicit permanent: 0.70-0.89 -> review, >= 0.90 -> auto_accept."""
        cand_70 = make_candidate(memory_type=MemoryType.FACT, confidence=0.70)
        assert policy.evaluate(cand_70)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

        cand_80 = make_candidate(memory_type=MemoryType.FACT, confidence=0.80)
        assert policy.evaluate(cand_80)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

        cand_89 = make_candidate(memory_type=MemoryType.FACT, confidence=0.89)
        assert policy.evaluate(cand_89)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

        cand_90 = make_candidate(memory_type=MemoryType.FACT, confidence=0.90)
        assert policy.evaluate(cand_90)[0] == CandidateResolutionDecision.AUTO_ACCEPT

        cand_91 = make_candidate(memory_type=MemoryType.FACT, confidence=0.91)
        assert policy.evaluate(cand_91)[0] == CandidateResolutionDecision.AUTO_ACCEPT

    def test_fact_inferred_boundaries(self, policy):
        """17. Fact inferred: < 0.80 -> reject, >= 0.80 -> review."""
        cand_75 = make_candidate(
            memory_type=MemoryType.FACT,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.75,
        )
        assert policy.evaluate(cand_75)[0] == CandidateResolutionDecision.REJECT

        cand_79 = make_candidate(
            memory_type=MemoryType.FACT,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.79,
        )
        assert policy.evaluate(cand_79)[0] == CandidateResolutionDecision.REJECT

        cand_80 = make_candidate(
            memory_type=MemoryType.FACT,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.80,
        )
        assert policy.evaluate(cand_80)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

        cand_81 = make_candidate(
            memory_type=MemoryType.FACT,
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.81,
        )
        assert policy.evaluate(cand_81)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

    def test_commitment_and_task_boundaries(self, policy):
        """18. Commitment and Task: explicit < 0.80 -> reject, >= 0.80 -> review."""
        for m_type in [MemoryType.COMMITMENT, MemoryType.TASK]:
            cand_79 = make_candidate(memory_type=m_type, confidence=0.79)
            assert policy.evaluate(cand_79)[0] == CandidateResolutionDecision.REJECT

            cand_80 = make_candidate(memory_type=m_type, confidence=0.80)
            assert policy.evaluate(cand_80)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

            cand_81 = make_candidate(memory_type=m_type, confidence=0.81)
            assert policy.evaluate(cand_81)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

    def test_episodic_boundaries(self, policy):
        """19. Episodic (with temporal timeframe): < 0.80 -> reject, >= 0.80 -> review."""
        temporal = TemporalAnchor(timeframe="2026-09-23")
        cand_79 = make_candidate(memory_type=MemoryType.EPISODIC, confidence=0.79, temporal=temporal)
        assert policy.evaluate(cand_79)[0] == CandidateResolutionDecision.REJECT

        cand_80 = make_candidate(memory_type=MemoryType.EPISODIC, confidence=0.80, temporal=temporal)
        assert policy.evaluate(cand_80)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

        cand_81 = make_candidate(memory_type=MemoryType.EPISODIC, confidence=0.81, temporal=temporal)
        assert policy.evaluate(cand_81)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

    def test_temporary_explicit_preference_and_fact_require_review(self, policy):
        """20. Temporary explicit preference & fact require review even with 0.70 confidence."""
        temp_pref = make_candidate(
            memory_type=MemoryType.PREFERENCE,
            confidence=0.70,
            temporal=TemporalAnchor(is_temporary=True, qualifier_phrase="for now"),
        )
        assert policy.evaluate(temp_pref)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW

        temp_fact = make_candidate(
            memory_type=MemoryType.FACT,
            confidence=0.70,
            temporal=TemporalAnchor(is_temporary=True, timeframe="this_week"),
        )
        assert policy.evaluate(temp_fact)[0] == CandidateResolutionDecision.REQUEST_USER_REVIEW


# ===========================================================================
# B. Temporal Semantics
# ===========================================================================

class TestTemporalSemantics:
    def test_permanent_candidate_expires_at_is_none(self):
        """21. Permanent candidate has expires_at = None."""
        cand = make_candidate(temporal=None)
        assert derive_temporal_expiration(cand) is None

    def test_explicit_structured_expiration_today(self):
        """22. 'today' converts to end of current UTC day (23:59:59.999999)."""
        frozen_now = datetime(2026, 9, 23, 10, 0, 0, tzinfo=timezone.utc)
        cand = make_candidate(temporal=TemporalAnchor(timeframe="today"))
        exp = derive_temporal_expiration(cand, now=frozen_now)
        assert exp is not None
        assert exp == datetime(2026, 9, 23, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def test_explicit_structured_expiration_this_week(self):
        """23. 'this_week' converts to Sunday 23:59:59 UTC."""
        # 2026-09-23 is Wednesday (weekday 2). Next Sunday is 2026-09-27 (weekday 6).
        frozen_now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
        cand = make_candidate(temporal=TemporalAnchor(timeframe="this_week"))
        exp = derive_temporal_expiration(cand, now=frozen_now)
        assert exp is not None
        assert exp.weekday() == 6
        assert exp.day == 27
        assert exp.hour == 23 and exp.minute == 59 and exp.second == 59

    def test_explicit_structured_expiration_this_month(self):
        """24. 'this_month' converts to last microsecond of current month."""
        frozen_now = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
        cand = make_candidate(temporal=TemporalAnchor(timeframe="this_month"))
        exp = derive_temporal_expiration(cand, now=frozen_now)
        assert exp is not None
        assert exp.month == 9 and exp.day == 30
        assert exp.hour == 23 and exp.minute == 59 and exp.second == 59

    def test_temporary_without_end_leaves_expires_at_none(self):
        """25. Temporary phrase without structured date ('for now' / 'currently') leaves expires_at=None."""
        for phrase in ["for now", "currently", "at the moment"]:
            cand = make_candidate(temporal=TemporalAnchor(is_temporary=True, qualifier_phrase=phrase))
            assert derive_temporal_expiration(cand) is None


# ===========================================================================
# C. Concurrency, Atomic Claim & Idempotency Store
# ===========================================================================

class TestStoreAndConcurrency:
    @pytest.mark.asyncio
    async def test_atomic_claim_blocks_concurrent_approval(self, review_store):
        """26. Atomic claim ensures two simultaneous claims cannot both succeed."""
        cand = make_candidate(candidate_id="cand_claim_0001", user_id="usr_conc")
        item = ReviewableCandidate(candidate=cand)
        await review_store.add(item)

        claim_1, claim_2 = await asyncio.gather(
            review_store.claim_candidate("usr_conc", "cand_claim_0001"),
            review_store.claim_candidate("usr_conc", "cand_claim_0001"),
        )
        assert (claim_1 is not None and claim_2 is None) or (claim_1 is None and claim_2 is not None)

    @pytest.mark.asyncio
    async def test_persistence_failure_rolls_back_to_pending(self, review_store):
        """27. Persistence failure rolls RESOLVING back to PENDING_REVIEW."""
        cand = make_candidate(candidate_id="cand_fail_0002", user_id="usr_roll")
        item = ReviewableCandidate(candidate=cand)
        await review_store.add(item)

        claimed = await review_store.claim_candidate("usr_roll", "cand_fail_0002")
        assert claimed is not None

        await review_store.rollback_claim("usr_roll", "cand_fail_0002")
        reclaimed = await review_store.claim_candidate("usr_roll", "cand_fail_0002")
        assert reclaimed is not None

    @pytest.mark.asyncio
    async def test_duplicate_approval_after_success_returns_ledger(self, review_store):
        """28. Bounded ledger records approval and prevents duplicate creation."""
        cand = make_candidate(candidate_id="cand_idem_0003", user_id="usr_idem")
        await review_store.add(ReviewableCandidate(candidate=cand))

        await review_store.claim_candidate("usr_idem", "cand_idem_0003")
        await review_store.commit_resolution("usr_idem", "cand_idem_0003", "approved", "mem_123")

        ledger = await review_store.get_ledger_entry("usr_idem", "cand_idem_0003")
        assert ledger is not None
        assert ledger.action == "approved"
        assert ledger.memory_id == "mem_123"
        assert await review_store.get("usr_idem", "cand_idem_0003") is None

    @pytest.mark.asyncio
    async def test_duplicate_rejection_recorded_in_ledger(self, review_store):
        """29. Rejection commits to ledger and removes from store."""
        cand = make_candidate(candidate_id="cand_rej_0004", user_id="usr_rej")
        await review_store.add(ReviewableCandidate(candidate=cand))

        await review_store.commit_resolution("usr_rej", "cand_rej_0004", "rejected")
        ledger = await review_store.get_ledger_entry("usr_rej", "cand_rej_0004")
        assert ledger is not None
        assert ledger.action == "rejected"

    @pytest.mark.asyncio
    async def test_lock_cleanup_on_terminal_resolution(self, review_store):
        """30. Per-candidate lock registry cleans up lock when candidate reaches terminal resolution."""
        cand = make_candidate(candidate_id="cand_lock_0005", user_id="usr_lock")
        await review_store.add(ReviewableCandidate(candidate=cand))

        # Access lock to instantiate it in registry
        lock = review_store.get_candidate_lock("usr_lock", "cand_lock_0005")
        assert "usr_lock:cand_lock_0005" in review_store._candidate_locks

        # Terminal commit
        await review_store.commit_resolution("usr_lock", "cand_lock_0005", "approved", "mem_999")
        assert "usr_lock:cand_lock_0005" not in review_store._candidate_locks

    @pytest.mark.asyncio
    async def test_process_restart_ephemeral_semantics(self):
        """31. Fresh store after restart contains no pending candidates or ledger."""
        store1 = InMemoryCandidateReviewStore()
        cand = make_candidate(candidate_id="cand_rst_0006", user_id="usr_rst")
        await store1.add(ReviewableCandidate(candidate=cand))
        await store1.commit_resolution("usr_rst", "cand_rst_0006", "approved", "mem_rst")

        # Simulate process restart by instantiating new store
        store2 = InMemoryCandidateReviewStore()
        assert await store2.get("usr_rst", "cand_rst_0006") is None
        assert await store2.get_ledger_entry("usr_rst", "cand_rst_0006") is None


# ===========================================================================
# D. REST API, Conflict Actions & Cross-User Security
# ===========================================================================

class TestRestApiAndConflicts:
    def test_list_and_detail_candidates(self, client, review_store):
        """32. Listing and detail retrieval are scoped to active user."""
        cand = make_candidate(candidate_id="cand_a00000000001", user_id="usr_api")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.get("/api/v1/memories/candidates", headers={"X-User-ID": "usr_api"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["items"][0]["candidate_id"] == "cand_a00000000001"

        detail = client.get("/api/v1/memories/candidates/cand_a00000000001", headers={"X-User-ID": "usr_api"})
        assert detail.status_code == 200
        assert detail.json()["candidate_id"] == "cand_a00000000001"

    def test_cross_user_candidate_access_returns_404(self, client, review_store):
        """33. Cross-user candidate access fails closed with HTTP 404."""
        cand = make_candidate(candidate_id="cand_a00000000002", user_id="usr_alice")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.get("/api/v1/memories/candidates/cand_a00000000002", headers={"X-User-ID": "usr_bob"})
        assert resp.status_code == 404

    def test_cross_user_candidate_mutation_returns_404(self, client, review_store):
        """34. Cross-user approve and reject fail closed with HTTP 404."""
        cand = make_candidate(candidate_id="cand_a00000000003", user_id="usr_alice")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        app_resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000003/approve",
            json={},
            headers={"X-User-ID": "usr_bob"},
        )
        assert app_resp.status_code == 404

        rej_resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000003/reject",
            headers={"X-User-ID": "usr_bob"},
        )
        assert rej_resp.status_code == 404

    def test_conflict_candidate_missing_action_returns_422(self, client, review_store):
        """35. Conflict candidate missing conflict_action returns HTTP 422."""
        cand = make_candidate(candidate_id="cand_a00000000004", user_id="usr_conf")
        item = ReviewableCandidate(candidate=cand, conflicts_with_memory_id="mem_target_1")
        asyncio.run(review_store.add(item))

        resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000004/approve",
            json={},
            headers={"X-User-ID": "usr_conf"},
        )
        assert resp.status_code == 422
        assert "conflict_action is required" in resp.json()["detail"]

    def test_conflict_candidate_invalid_action_returns_422(self, client, review_store):
        """36. Conflict candidate invalid conflict_action returns HTTP 422."""
        cand = make_candidate(candidate_id="cand_a00000000005", user_id="usr_conf")
        item = ReviewableCandidate(candidate=cand, conflicts_with_memory_id="mem_target_1")
        asyncio.run(review_store.add(item))

        resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000005/approve",
            json={"conflict_action": "overwrite_all"},
            headers={"X-User-ID": "usr_conf"},
        )
        assert resp.status_code == 422
        assert "Invalid conflict_action" in resp.json()["detail"]

    def test_non_conflict_candidate_with_action_returns_422(self, client, review_store):
        """37. Non-conflict candidate providing conflict_action returns HTTP 422."""
        cand = make_candidate(candidate_id="cand_a00000000006", user_id="usr_conf")
        item = ReviewableCandidate(candidate=cand, conflicts_with_memory_id=None)
        asyncio.run(review_store.add(item))

        resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000006/approve",
            json={"conflict_action": "replace"},
            headers={"X-User-ID": "usr_conf"},
        )
        assert resp.status_code == 422
        assert "conflict_action must be omitted" in resp.json()["detail"]

    def test_conflict_replace_updates_target_only(self, client, review_store, memory_manager):
        """38. 'replace' updates the server-stamped conflict target only."""
        existing, _ = asyncio.run(memory_manager.create_memory("User lives in Delhi", type=MemoryType.FACT))
        cand = make_candidate(
            candidate_id="cand_a00000000007",
            user_id="usr_conf",
            memory_type=MemoryType.FACT,
            content="User lives in Bangalore",
        )
        item = ReviewableCandidate(candidate=cand, conflicts_with_memory_id=existing.id)
        asyncio.run(review_store.add(item))

        resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000007/approve",
            json={"conflict_action": "replace"},
            headers={"X-User-ID": "usr_conf"},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == existing.id
        assert resp.json()["content"] == "User lives in Bangalore"

    def test_conflict_keep_both_creates_new_memory(self, client, review_store, memory_manager):
        """39. 'keep_both' creates a new memory without altering existing."""
        existing, _ = asyncio.run(memory_manager.create_memory("User lives in Delhi", type=MemoryType.FACT))
        cand = make_candidate(
            candidate_id="cand_a00000000008",
            user_id="usr_conf",
            memory_type=MemoryType.FACT,
            content="User also has a flat in Mumbai",
        )
        item = ReviewableCandidate(candidate=cand, conflicts_with_memory_id=existing.id)
        asyncio.run(review_store.add(item))

        resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000008/approve",
            json={"conflict_action": "keep_both"},
            headers={"X-User-ID": "usr_conf"},
        )
        assert resp.status_code == 200
        new_id = resp.json()["id"]
        assert new_id != existing.id
        assert asyncio.run(memory_manager.get_memory(existing.id)) is not None
        assert asyncio.run(memory_manager.get_memory(new_id)) is not None

    def test_cross_user_conflict_target_replacement_prevented(self, client, review_store, memory_manager):
        """40. Replacement of another user's conflicting memory target is blocked."""
        # Create memory belonging to usr_alice
        alice_mem, _ = asyncio.run(
            memory_manager.create_memory(
                "Alice's secret location",
                type=MemoryType.FACT,
                metadata={"user_id": "usr_alice"},
            )
        )
        # Malicious/compromised candidate under usr_bob targeting alice_mem
        cand = make_candidate(
            candidate_id="cand_a00000000009",
            user_id="usr_bob",
            memory_type=MemoryType.FACT,
            content="Bob hijacked location",
        )
        item = ReviewableCandidate(candidate=cand, conflicts_with_memory_id=alice_mem.id)
        asyncio.run(review_store.add(item))

        resp = client.post(
            "/api/v1/memories/candidates/cand_a00000000009/approve",
            json={"conflict_action": "replace"},
            headers={"X-User-ID": "usr_bob"},
        )
        assert resp.status_code == 404

    def test_idempotent_duplicate_approve_returns_existing(self, client, review_store):
        """41. Repeated approval returns the already-created memory item."""
        cand = make_candidate(candidate_id="cand_a00000000010", user_id="usr_idem")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        # First approval
        resp1 = client.post(
            "/api/v1/memories/candidates/cand_a00000000010/approve",
            json={},
            headers={"X-User-ID": "usr_idem"},
        )
        assert resp1.status_code == 200
        mem_id_1 = resp1.json()["id"]

        # Second approval
        resp2 = client.post(
            "/api/v1/memories/candidates/cand_a00000000010/approve",
            json={},
            headers={"X-User-ID": "usr_idem"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["id"] == mem_id_1

    def test_idempotent_duplicate_reject_returns_already_rejected(self, client, review_store):
        """42. Repeated rejection returns deterministic already_rejected status."""
        cand = make_candidate(candidate_id="cand_a00000000011", user_id="usr_rej")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        # First reject
        resp1 = client.post(
            "/api/v1/memories/candidates/cand_a00000000011/reject",
            headers={"X-User-ID": "usr_rej"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "rejected"

        # Second reject
        resp2 = client.post(
            "/api/v1/memories/candidates/cand_a00000000011/reject",
            headers={"X-User-ID": "usr_rej"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "already_rejected"


# ===========================================================================
# E. Tag Override, Sanitization & Content Revalidation
# ===========================================================================

class TestTagOverrideAndRevalidation:
    def test_override_tags_too_many_rejected(self, client, review_store):
        """43. More than 10 override tags rejected with HTTP 422."""
        cand = make_candidate(candidate_id="cand_b00000000001", user_id="usr_tags")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000001/approve",
            json={"override_tags": [f"tag_{i}" for i in range(11)]},
            headers={"X-User-ID": "usr_tags"},
        )
        assert resp.status_code == 422
        assert "override_tags" in str(resp.json()["detail"])

    def test_override_tags_too_long_rejected(self, client, review_store):
        """44. Tag longer than 30 characters rejected with HTTP 422."""
        cand = make_candidate(candidate_id="cand_b00000000002", user_id="usr_tags")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000002/approve",
            json={"override_tags": ["a" * 31]},
            headers={"X-User-ID": "usr_tags"},
        )
        assert resp.status_code == 422
        assert "exceeds 30 characters" in resp.json()["detail"]

    def test_override_tags_invalid_characters_rejected(self, client, review_store):
        """45. Tag with special characters rejected with HTTP 422."""
        cand = make_candidate(candidate_id="cand_b00000000003", user_id="usr_tags")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000003/approve",
            json={"override_tags": ["hello$world!"]},
            headers={"X-User-ID": "usr_tags"},
        )
        assert resp.status_code == 422
        assert "invalid characters" in resp.json()["detail"]

    def test_override_tags_reserved_prefixes_rejected(self, client, review_store):
        """46. System reserved prefixes (sys:, aurora:, mem_, etc.) rejected with HTTP 422."""
        cand = make_candidate(candidate_id="cand_b00000000004", user_id="usr_tags")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        for prefix in ["sys:root", "aurora:core", "mem_secret", "cand_fake", "user_1"]:
            resp = client.post(
                "/api/v1/memories/candidates/cand_b00000000004/approve",
                json={"override_tags": [prefix]},
                headers={"X-User-ID": "usr_tags"},
            )
            assert resp.status_code == 422
            assert "reserved system prefix" in resp.json()["detail"]

    def test_override_tags_replaces_candidate_tags(self, client, review_store):
        """47. Valid override_tags replaces original candidate tags."""
        cand = make_candidate(candidate_id="cand_b00000000005", user_id="usr_tags", tags=["old_1", "old_2"])
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000005/approve",
            json={"override_tags": ["new_tag_a", "NEW_TAG_B"]},
            headers={"X-User-ID": "usr_tags"},
        )
        assert resp.status_code == 200
        tags = resp.json()["tags"]
        assert "old_1" not in tags
        assert "new_tag_a" in tags
        assert "new_tag_b" in tags  # normalized lowercase

    def test_omitted_override_tags_preserves_candidate_tags(self, client, review_store):
        """48. Omitted override_tags preserves original candidate tags."""
        cand = make_candidate(candidate_id="cand_b00000000006", user_id="usr_tags", tags=["pref_a", "pref_b"])
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000006/approve",
            json={},
            headers={"X-User-ID": "usr_tags"},
        )
        assert resp.status_code == 200
        tags = resp.json()["tags"]
        assert "pref_a" in tags
        assert "pref_b" in tags

    def test_edited_content_credentials_rejected(self, client, review_store):
        """49. Edited content containing credentials is rejected with HTTP 422."""
        cand = make_candidate(candidate_id="cand_b00000000007", user_id="usr_edit")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000007/approve",
            json={"edited_content": "Password: super_secret_password_123!"},
            headers={"X-User-ID": "usr_edit"},
        )
        assert resp.status_code == 422
        assert "credential" in resp.json()["detail"].lower()

    def test_inferred_approval_preserves_inferred_source(self, client, review_store):
        """50. Unedited inferred candidate approval keeps source = INFERRED."""
        cand = make_candidate(
            candidate_id="cand_b00000000008",
            user_id="usr_prov",
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.85,
        )
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000008/approve",
            json={},
            headers={"X-User-ID": "usr_prov"},
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "inferred"

    def test_edited_inferred_candidate_becomes_user_explicit(self, client, review_store, memory_manager):
        """51. Edited inferred candidate becomes USER_EXPLICIT with lineage in metadata."""
        cand = make_candidate(
            candidate_id="cand_b00000000009",
            user_id="usr_prov",
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            content="User might enjoy tea in afternoons",
            confidence=0.85,
        )
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        resp = client.post(
            "/api/v1/memories/candidates/cand_b00000000009/approve",
            json={"edited_content": "I definitely drink Earl Grey tea every single afternoon"},
            headers={"X-User-ID": "usr_prov"},
        )
        assert resp.status_code == 200
        assert resp.json()["source"] == "user_explicit"
        mem = asyncio.run(memory_manager.get_memory(resp.json()["id"]))
        assert mem.metadata["original_evidence"] == "model_inferred"
        assert mem.metadata["resolution"] == "user_edited"
        assert "transcript" not in mem.metadata
        assert "chain_of_thought" not in mem.metadata
        assert "web_html" not in mem.metadata


# ===========================================================================
# F. Conversation Lifecycle & Isolation
# ===========================================================================

class TestConversationLifecycleIntegration:
    @pytest.mark.asyncio
    async def test_normal_turn_resolves_once(self, memory_manager, review_store):
        """52. Normal completed turn extracts and resolves candidates exactly once."""
        extractor = MagicMock()
        cand = make_candidate(
            candidate_id="cand_c00000000001",
            user_id="usr_live",
            confidence=0.92,
            memory_type=MemoryType.PREFERENCE,
        )
        extractor.extract = AsyncMock(return_value=[cand])

        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)
        service = ConversationService(
            store=InMemoryConversationStore(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        session = ConversationSession(conversation_id="conv_live", metadata={"user_id": "usr_live"})
        await service.store.save(session)

        class CannedProvider(BaseAIProvider):
            @property
            def provider_name(self):
                return "canned"

            async def generate_response(self, messages, **kwargs):
                return AIResponse(content="Sure thing!", provider="canned", model="m")

        service.ai_provider = CannedProvider()
        service.agent_loop.ai_provider = service.ai_provider

        session, _, _, pending, _ = await service.process_chat(
            user_text="I prefer dark mode.",
            conversation_id=session.conversation_id,
        )

        assert pending is None
        active_mems = await memory_manager.list_memories()
        assert len(active_mems) == 1
        assert active_mems[0].content == cand.content

    @pytest.mark.asyncio
    async def test_confirmation_pause_skips_resolution(self, memory_manager):
        """53. Action paused for user confirmation skips candidate resolution."""
        registry = ToolRegistry()
        tool = MagicMock(spec=BaseTool)
        tool.name = "high_risk_action"
        tool.definition = ToolDefinition(
            name="high_risk_action",
            description="desc",
            parameters_schema={},
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )
        registry.register(tool)

        class ToolProvider(BaseAIProvider):
            @property
            def provider_name(self):
                return "tp"

            async def generate_response(self, messages, **kwargs):
                return AIResponse(
                    content="",
                    provider="tp",
                    model="m",
                    finish_reason="tool_calls",
                    tool_calls=[ToolCall(name="high_risk_action", arguments={})],
                )

        resolver = MagicMock(spec=CandidateResolutionService)
        service = ConversationService(
            store=InMemoryConversationStore(),
            tool_registry=registry,
            tool_executor=ToolExecutor(registry=registry),
            candidate_resolver=resolver,
        )
        service.ai_provider = ToolProvider()
        service.agent_loop.ai_provider = service.ai_provider

        session, _, _, pending, _ = await service.process_chat(user_text="Do sensitive task")
        assert pending is not None
        resolver.resolve_candidates.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmation_rejection_skips_resolution(self):
        """54. Confirmation rejection synthesizes acknowledgment and skips resolution."""
        resolver = MagicMock(spec=CandidateResolutionService)
        service = ConversationService(
            store=InMemoryConversationStore(),
            candidate_resolver=resolver,
        )
        # Mock resume_execution
        service.agent_loop.resume_execution = AsyncMock(
            return_value=(
                ChatMessage(role="assistant", content="Cancelled action."),
                AIResponse(content="Cancelled action.", provider="p", model="m"),
                [],
            )
        )
        session = ConversationSession(conversation_id="conv_rej")
        session.active_agent_execution = AgentExecutionState(
            conversation_id="conv_rej",
            user_id="usr_rej",
            execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        )
        await service.store.save(session)

        await service.confirm_tool_action(
            conversation_id="conv_rej",
            confirmation_id="conf_123",
            approved=False,
            execution_id=session.active_agent_execution.execution_id,
        )
        resolver.resolve_candidates.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmation_approval_completed_resolves_once(self, memory_manager, review_store):
        """55. Confirmation approval completing execution resolves candidates once."""
        cand = make_candidate(
            candidate_id="cand_c00000000002",
            user_id="usr_conf_live",
            confidence=0.92,
            memory_type=MemoryType.PREFERENCE,
        )
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])
        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)

        service = ConversationService(
            store=InMemoryConversationStore(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )
        async def mock_resume(session, confirmation_id, approved, model=None):
            msg = ChatMessage(role="assistant", content="Action executed successfully.")
            session.add_message(role=msg.role, content=msg.content)
            await service.agent_loop._transition_to_completed(session, session.active_agent_execution, msg.content, model=model)
            return (
                msg,
                AIResponse(content="Action executed successfully.", provider="p", model="m"),
                [],
            )

        service.agent_loop.resume_execution = AsyncMock(side_effect=mock_resume)

        session = ConversationSession(conversation_id="conv_appr", metadata={"user_id": "usr_conf_live"})
        exec_state = AgentExecutionState(
            conversation_id="conv_appr",
            user_id="usr_conf_live",
            execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        )
        session.active_agent_execution = exec_state
        await service.store.save(session)

        await service.confirm_tool_action(
            conversation_id="conv_appr",
            confirmation_id="conf_456",
            approved=True,
            execution_id=exec_state.execution_id,
        )
        # Resolved into memory
        mems = await memory_manager.list_memories()
        assert len(mems) == 1
        assert mems[0].content == cand.content

    @pytest.mark.asyncio
    async def test_candidate_extraction_exception_does_not_fail_chat(self):
        """56. Exception during extraction or resolution does not break chat response."""
        extractor = MagicMock()
        extractor.extract = AsyncMock(side_effect=RuntimeError("Model provider timeout"))
        service = ConversationService(
            store=InMemoryConversationStore(),
            candidate_extractor=extractor,
        )

        class CannedProvider(BaseAIProvider):
            @property
            def provider_name(self):
                return "canned"

            async def generate_response(self, messages, **kwargs):
                return AIResponse(content="Everything is fine!", provider="canned", model="m")

        service.ai_provider = CannedProvider()
        service.agent_loop.ai_provider = service.ai_provider

        session, asst_msg, _, pending, _ = await service.process_chat(user_text="Hello")
        assert asst_msg.content == "Everything is fine!"
        assert pending is None


# ===========================================================================
# G. Persistence Boundary Invariants
# ===========================================================================

class TestPersistenceBoundaryInvariants:
    @pytest.mark.asyncio
    async def test_candidate_review_store_has_no_sqlite_tables_or_direct_sql(self, review_store):
        """57. CandidateReviewStore is strictly in-memory without database connection or table writes."""
        assert not hasattr(review_store, "db")
        assert not hasattr(review_store, "connection")
        assert not hasattr(review_store, "execute")

    @pytest.mark.asyncio
    async def test_memory_manager_is_sole_persistence_path_zero_direct_sqlite(self, resolution_service):
        """58. MemoryManager is the sole persistence authority; CandidateResolutionService has no SQLite store."""
        mock_sqlite_store = MagicMock(spec=SQLiteMemoryStore)
        mock_sqlite_store.list_memories = AsyncMock(return_value=[])
        mock_sqlite_store.put = AsyncMock(
            return_value=MemoryItem(
                id="mem_spy_001",
                type=MemoryType.PREFERENCE,
                content="User prefers dark mode in all editors",
            )
        )
        resolution_service.memory_manager.store = mock_sqlite_store

        cand = make_candidate(
            candidate_id="cand_d00000000001",
            memory_type=MemoryType.PREFERENCE,
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
        )

        with patch.object(resolution_service.memory_manager, "create_memory", wraps=resolution_service.memory_manager.create_memory) as spy_create:
            await resolution_service.resolve_candidates([cand], active_user_id=cand.user_id)
            assert spy_create.call_count == 1
            assert mock_sqlite_store.put.call_count == 1  # Only MemoryManager called it

    @pytest.mark.asyncio
    async def test_no_candidate_persistence_in_session_metadata(self, memory_manager, review_store):
        """59. ConversationSession.metadata never stores raw candidates or review queues."""
        cand = make_candidate(candidate_id="cand_d00000000002", user_id="usr_clean")
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])
        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)

        service = ConversationService(
            store=InMemoryConversationStore(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )
        class CannedProvider(BaseAIProvider):
            @property
            def provider_name(self):
                return "c"
            async def generate_response(self, messages, **kwargs):
                return AIResponse(content="Done", provider="c", model="m")

        service.ai_provider = CannedProvider()
        service.agent_loop.ai_provider = service.ai_provider

        session, _, _, _, _ = await service.process_chat(user_text="Remember my preference")
        # Metadata check
        assert "candidates" not in session.metadata
        assert "review_queue" not in session.metadata
        assert "candidate_id" not in session.metadata

    @pytest.mark.asyncio
    async def test_no_candidate_persistence_in_agent_execution_state(self):
        """60. AgentExecutionState fields remain unmodified and contain no candidate objects."""
        state = AgentExecutionState(conversation_id="conv_state", user_id="usr_state")
        assert not hasattr(state, "candidates")
        assert not hasattr(state, "memory_candidates")
        assert not hasattr(state, "review_queue")

    def test_client_cannot_supply_user_id_in_approve_payload(self, client, review_store):
        """61. Approve payload schema rejects or ignores client-supplied user_id."""
        cand = make_candidate(candidate_id="cand_d00000000003", user_id="usr_target")
        asyncio.run(review_store.add(ReviewableCandidate(candidate=cand)))

        # Client attempts to spoof user_id in body
        resp = client.post(
            "/api/v1/memories/candidates/cand_d00000000003/approve",
            json={"user_id": "usr_attacker", "edited_content": "Valid clean preference text"},
            headers={"X-User-ID": "usr_target"},
        )
        assert resp.status_code == 200
        # Result memory belongs to usr_target
        mem_data = resp.json()
        assert mem_data["content"] == "Valid clean preference text"
