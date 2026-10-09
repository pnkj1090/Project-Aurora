"""
Deterministic validation pipeline for Phase 5L.1 — Memory Candidate Extraction.
Enforces the 6 approved validation gates.
Separates ModelCandidateProposal into CandidateValidationResult and accepted MemoryCandidate.

CRITICAL INVARIANTS:
1. Model is NEVER authoritative for server metadata (candidate_id, user_id, conversation_id, timestamps).
2. USER_EXPLICIT candidates must be conservatively grounded in user message.
3. No credentials, tokens, internal IDs, or web citations allowed.
4. Accepted MemoryCandidate does NOT contain is_valid or rejection_reason.
"""
import re
import secrets
from datetime import datetime, timezone
from typing import Callable, List, Optional, Set

from app.services.memory.candidates.models import (
    CandidateEvidenceType,
    CandidateSensitivity,
    CandidateValidationResult,
    MemoryCandidate,
    ModelCandidateProposal,
    TemporalAnchor,
)
from app.services.memory.schemas import MemoryType
from app.services.web.privacy.policy import (
    CREDENTIAL_PATTERNS,
    PROHIBITED_ID_PATTERNS,
    GPS_COORDINATES_PATTERN,
    COARSE_GPS_COORDINATES_PATTERN,
)

# Citation marker pattern from Phase 5K.4
_CITATION_MARKER_PATTERN = re.compile(r"\[c_[0-9a-f]{8}_\d+\]")

# Conversational filler and noise patterns
_CONVERSATIONAL_BANTER_PATTERNS = [
    re.compile(r"^(hi|hello|hey|good\s+(morning|afternoon|evening|day)|greetings)\b", re.IGNORECASE),
    re.compile(r"^(thanks|thank\s+you|thx|cheers|appreciated)\b", re.IGNORECASE),
    re.compile(r"^(bye|goodbye|see\s+ya|catch\s+you\s+later)\b", re.IGNORECASE),
    re.compile(r"^(ok|okay|cool|awesome|great|got\s+it|understood|sure)\b", re.IGNORECASE),
]

# Sensitive domain keywords (health, financial, credentials, sensitive personal)
_SENSITIVE_KEYWORDS: Set[str] = {
    # Health / Medical
    "diagnosed", "diagnosis", "disease", "medication", "prescription", "allergy", "allergic",
    "doctor", "therapy", "depressed", "depression", "cancer", "diabetes", "bipolar",
    "hospital", "psychiatrist", "medical", "treatment",
    # Financial
    "bank", "salary", "credit card", "debit card", "account number", "routing number",
    "net worth", "pin code", "ssn", "social security", "investment", "portfolio balance",
    # Credentials / Secrets
    "password", "secret key", "passcode", "access code", "token", "credentials",
    # Intimate / Sensitive
    "affair", "confidential", "arrested", "lawsuit", "illegal",
}

# Negation terms indicating negative polarity
_NEGATION_TERMS = [
    "don't", "dont", "do not", "never", "hate", "dislike", "cannot stand", "cant stand",
    "won't", "wont", "not a fan", "prefer not", "avoid", "stop", "refuse",
]

# Positive terms asserting affirmative preference or habit
_POSITIVE_AFFINITY_TERMS = [
    "likes", "prefers", "loves", "enjoys", "wants", "drinks", "eats", "favors", "fond of",
]


def default_candidate_id_generator() -> str:
    """Generate server-controlled candidate ID: cand_<12hex>."""
    return f"cand_{secrets.token_hex(6)}"


class CandidateValidationPipeline:
    """
    Deterministic validation pipeline for raw candidate proposals.
    Evaluates proposals against 6 sequential gates.
    """

    def __init__(
        self,
        candidate_id_generator: Optional[Callable[[], str]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.candidate_id_generator = candidate_id_generator or default_candidate_id_generator
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def normalize_content(self, text: str) -> str:
        """Strip excess whitespace and standard formatting."""
        return " ".join(text.strip().split())

    def normalize_tags(self, tags: Optional[List[str]]) -> List[str]:
        """Normalize, lowercase, and deduplicate tag lists."""
        if not tags:
            return []
        cleaned = set()
        for t in tags:
            stripped = t.strip().lower().lstrip("#")
            if stripped and len(stripped) <= 50:
                cleaned.add(stripped)
        return sorted(list(cleaned))[:10]

    def _check_polarity_inversion(self, user_text: str, candidate_content: str) -> bool:
        """
        Returns True if a polarity inversion is detected:
        e.g., user says 'I don't like coffee' but candidate says 'User likes coffee'.
        """
        user_lower = user_text.lower()
        cand_lower = candidate_content.lower()

        user_has_negation = any(neg in user_lower for neg in _NEGATION_TERMS)
        cand_has_positive = any(pos in cand_lower for pos in _POSITIVE_AFFINITY_TERMS)
        cand_has_negation = any(neg in cand_lower for neg in _NEGATION_TERMS)

        # User negative on topic, but candidate asserts positive affinity without negation
        if user_has_negation and cand_has_positive and not cand_has_negation:
            return True

        return False

    def _check_uncertainty_inflation(self, user_text: str, proposal: ModelCandidateProposal) -> bool:
        """
        Returns True if user expressed uncertainty but candidate asserted unconditional certainty.
        """
        uncertainty_markers = ["might", "maybe", "possibly", "considering", "could be", "thinking about", "if i "]
        user_lower = user_text.lower()
        has_uncertainty = any(m in user_lower for m in uncertainty_markers)

        if has_uncertainty:
            # If temporal anchor preserves uncertainty, it is grounded
            if proposal.temporal and (proposal.temporal.is_hypothetical or proposal.temporal.is_temporary):
                return False
            # Otherwise, candidate claims certainty on an uncertain user statement
            return True

        return False

    def _check_temporal_qualifier_preservation(self, user_text: str, proposal: ModelCandidateProposal) -> bool:
        """
        Returns False if user stated a time-bound qualifier ('this month', 'for now', 'currently')
        but candidate stripped it and claimed permanent validity.
        """
        temporal_markers = ["this month", "currently", "for now", "temporarily", "used to", "this week"]
        user_lower = user_text.lower()
        cand_lower = proposal.content.lower()

        user_marker = next((m for m in temporal_markers if m in user_lower), None)
        if user_marker:
            # If candidate content or temporal anchor includes the qualifier, it is preserved
            if user_marker in cand_lower:
                return True
            if proposal.temporal and (proposal.temporal.is_temporary or proposal.temporal.qualifier_phrase):
                return True
            return False  # Stripped qualifier

        return True

    def validate_content_text(
        self, content: str
    ) -> tuple[bool, Optional[str], str, CandidateSensitivity]:
        """
        Validates arbitrary candidate text against Gate 1 (length 5-500),
        Gate 4 (credentials, tokens, prohibited IDs, citations, GPS),
        Gate 5 (conversational banter and pure questions), and
        Gate 6 (sensitivity classification).
        Returns:
            (is_valid, rejection_reason, normalized_content, sensitivity)
        """
        clean_content = self.normalize_content(content)

        # Gate 1: Structure and Length
        if not clean_content or len(clean_content) < 5:
            return False, "Candidate content must be at least 5 characters.", clean_content, CandidateSensitivity.NORMAL

        if len(clean_content) > 500:
            return False, "Candidate content exceeds 500 characters limit.", clean_content, CandidateSensitivity.NORMAL

        # Gate 4: Credentials, Tokens, Prohibited IDs, Citations, GPS
        for cred_pattern in CREDENTIAL_PATTERNS:
            if cred_pattern.search(clean_content):
                return False, "Contains prohibited credential or token signature.", clean_content, CandidateSensitivity.NORMAL

        for id_pattern in PROHIBITED_ID_PATTERNS:
            if id_pattern.search(clean_content):
                return False, "Contains prohibited internal identifier.", clean_content, CandidateSensitivity.NORMAL

        if _CITATION_MARKER_PATTERN.search(clean_content):
            return False, "Contains raw citation marker; external citation cannot become user memory.", clean_content, CandidateSensitivity.NORMAL

        if GPS_COORDINATES_PATTERN.search(clean_content) or COARSE_GPS_COORDINATES_PATTERN.search(clean_content):
            return False, "Contains raw GPS coordinates; location telemetry cannot become user memory.", clean_content, CandidateSensitivity.NORMAL

        # Gate 5: Conversational Banter & Pure Questions
        for banter_pat in _CONVERSATIONAL_BANTER_PATTERNS:
            if banter_pat.match(clean_content):
                return False, "Candidate is conversational banter or filler.", clean_content, CandidateSensitivity.NORMAL

        if clean_content.endswith("?") and not any(term in clean_content.lower() for term in ["wondering", "asked", "questioning"]):
            return False, "Pure question cannot be a user memory candidate.", clean_content, CandidateSensitivity.NORMAL

        # Gate 6: Sensitivity Classification
        sensitivity = CandidateSensitivity.NORMAL
        content_lower = clean_content.lower()
        if any(kw in content_lower for kw in _SENSITIVE_KEYWORDS):
            sensitivity = CandidateSensitivity.SENSITIVE

        return True, None, clean_content, sensitivity

    def validate_proposal(
        self,
        proposal: ModelCandidateProposal,
        user_text: str,
        assistant_text: str,
        active_user_id: str,
        conversation_id: str,
        turn_index: int,
    ) -> CandidateValidationResult:
        """
        Validates a single ModelCandidateProposal through all 6 gates.
        Returns a CandidateValidationResult containing an accepted MemoryCandidate if valid,
        or rejection details if invalid.
        """
        errors: List[str] = []
        clean_content = self.normalize_content(proposal.content)

        # -------------------------------------------------------------
        # Gate 1: Structure and Length
        # -------------------------------------------------------------
        if not clean_content or len(clean_content) < 5:
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason="Candidate content must be at least 5 characters.",
                validation_errors=["content_too_short"],
            )

        if len(clean_content) > 500:
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason="Candidate content exceeds 500 characters limit.",
                validation_errors=["content_too_long"],
            )

        if proposal.confidence < 0.50:
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason=f"Candidate confidence ({proposal.confidence:.2f}) below threshold 0.50.",
                validation_errors=["low_confidence"],
            )

        if not isinstance(proposal.memory_type, MemoryType):
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason=f"Invalid memory type: {proposal.memory_type}",
                validation_errors=["invalid_memory_type"],
            )

        # -------------------------------------------------------------
        # Gate 4: Credential and Internal ID Protection (Check early)
        # -------------------------------------------------------------
        for cred_pattern in CREDENTIAL_PATTERNS:
            if cred_pattern.search(clean_content):
                return CandidateValidationResult(
                    proposal=proposal,
                    is_valid=False,
                    rejection_reason="Contains prohibited credential or token signature.",
                    validation_errors=["credential_detected"],
                )

        for id_pattern in PROHIBITED_ID_PATTERNS:
            if id_pattern.search(clean_content):
                return CandidateValidationResult(
                    proposal=proposal,
                    is_valid=False,
                    rejection_reason="Contains prohibited internal identifier.",
                    validation_errors=["internal_id_detected"],
                )

        # Check for citation marker leakage
        if _CITATION_MARKER_PATTERN.search(clean_content):
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason="Contains raw citation marker; external citation cannot become user memory.",
                validation_errors=["citation_marker_detected"],
            )

        # Check for GPS coordinates leakage
        if GPS_COORDINATES_PATTERN.search(clean_content) or COARSE_GPS_COORDINATES_PATTERN.search(clean_content):
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason="Contains raw GPS coordinates; location telemetry cannot become user memory.",
                validation_errors=["gps_coordinates_detected"],
            )

        # -------------------------------------------------------------
        # Gate 5: Conversational Noise & Hypothetical Handling
        # -------------------------------------------------------------
        for banter_pat in _CONVERSATIONAL_BANTER_PATTERNS:
            if banter_pat.match(clean_content):
                return CandidateValidationResult(
                    proposal=proposal,
                    is_valid=False,
                    rejection_reason="Candidate is conversational banter or filler.",
                    validation_errors=["conversational_banter"],
                )

        # Pure question check
        if clean_content.endswith("?") and not any(term in clean_content.lower() for term in ["wondering", "asked", "questioning"]):
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason="Pure question cannot be a user memory candidate.",
                validation_errors=["pure_question"],
            )

        # -------------------------------------------------------------
        # Gate 2: Evidence Grounding & Conservative Checks
        # -------------------------------------------------------------
        evidence_type = proposal.evidence_type
        user_lower = user_text.lower()
        asst_lower = assistant_text.lower()

        if evidence_type == CandidateEvidenceType.USER_EXPLICIT:
            if not proposal.user_quote or not proposal.user_quote.strip():
                # Missing quote: downgrade to MODEL_INFERRED
                evidence_type = CandidateEvidenceType.MODEL_INFERRED
            else:
                quote_clean = proposal.user_quote.strip().lower()
                # Quote must exist in user text
                if quote_clean not in user_lower:
                    # If quote appears only in assistant text, assistant cannot create USER_EXPLICIT
                    if quote_clean in asst_lower:
                        return CandidateValidationResult(
                            proposal=proposal,
                            is_valid=False,
                            rejection_reason="Quote originated from assistant response, not user. Assistant cannot create explicit user memory.",
                            validation_errors=["assistant_only_quote"],
                        )
                    # Quote not in user text at all -> reject
                    return CandidateValidationResult(
                        proposal=proposal,
                        is_valid=False,
                        rejection_reason="Quoted evidence was not found in user message.",
                        validation_errors=["quote_not_in_user_message"],
                    )

        # Polarity inversion check
        if self._check_polarity_inversion(user_text, clean_content):
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason="Polarity inversion detected: candidate contradicts user negation.",
                validation_errors=["polarity_inversion"],
            )

        # Uncertainty inflation check
        if self._check_uncertainty_inflation(user_text, proposal):
            # Downgrade to inferred or reject if claims certainty on speculation
            if evidence_type == CandidateEvidenceType.USER_EXPLICIT:
                evidence_type = CandidateEvidenceType.MODEL_INFERRED

        # Temporal qualifier check
        if not self._check_temporal_qualifier_preservation(user_text, proposal):
            return CandidateValidationResult(
                proposal=proposal,
                is_valid=False,
                rejection_reason="Temporal qualifier from user statement was stripped; candidate claims permanent fact.",
                validation_errors=["temporal_qualifier_stripped"],
            )

        # -------------------------------------------------------------
        # Gate 6: Sensitivity Classification
        # -------------------------------------------------------------
        sensitivity = proposal.sensitivity
        content_lower = clean_content.lower()
        if any(kw in content_lower for kw in _SENSITIVE_KEYWORDS):
            sensitivity = CandidateSensitivity.SENSITIVE

        # -------------------------------------------------------------
        # All Gates Passed: Construct Authoritative MemoryCandidate
        # -------------------------------------------------------------
        candidate_id = self.candidate_id_generator()
        extracted_at = self.clock()

        candidate = MemoryCandidate(
            candidate_id=candidate_id,
            user_id=active_user_id,
            conversation_id=conversation_id,
            turn_index=turn_index,
            extracted_at=extracted_at,
            extractor_version="5L.1",
            memory_type=proposal.memory_type,
            content=clean_content,
            evidence_type=evidence_type,
            confidence=max(0.0, min(1.0, proposal.confidence)),
            importance=max(1, min(5, proposal.importance)),
            tags=self.normalize_tags(proposal.tags),
            user_quote=proposal.user_quote.strip() if proposal.user_quote else None,
            temporal=proposal.temporal,
            sensitivity=sensitivity,
        )

        return CandidateValidationResult(
            proposal=proposal,
            is_valid=True,
            candidate=candidate,
        )

    def validate_proposals(
        self,
        proposals: List[ModelCandidateProposal],
        user_text: str,
        assistant_text: str,
        active_user_id: str,
        conversation_id: str,
        turn_index: int,
    ) -> List[CandidateValidationResult]:
        """
        Validates all candidate proposals and returns validation results.
        """
        results: List[CandidateValidationResult] = []
        for prop in proposals:
            res = self.validate_proposal(
                proposal=prop,
                user_text=user_text,
                assistant_text=assistant_text,
                active_user_id=active_user_id,
                conversation_id=conversation_id,
                turn_index=turn_index,
            )
            results.append(res)
        return results
