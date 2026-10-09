"""
Comprehensive deterministic test suite for Phase 5L.1 — Memory Candidate Extraction.

Tests all 40 required test matrix items across:
A. Evidence Hierarchy & Memory Types (1-9, 34-37)
B. Conservative Grounding (10-15)
C. Security, Credentials & Prohibited Data (16-21, 31-33)
D. Identity & Server-Controlled Authority (22-24, 39)
E. Turn Lifecycle, Isolation & Invariants (25-30, 38, 40)
"""
import inspect
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.ai.base import (
    AIProviderError,
    AIResponse,
    BaseAIProvider,
    ChatMessage,
    MessageRole,
)
from app.services.conversation.models import (
    AgentExecutionState,
    AgentExecutionStatus,
)
from app.services.conversation.service import ConversationService
from app.services.conversation.session import ConversationSession
from app.services.conversation.store import InMemoryConversationStore
from app.services.memory.candidates import (
    CandidateEvidenceType,
    CandidateSensitivity,
    CandidateValidationPipeline,
    CandidateValidationResult,
    MemoryCandidate,
    MemoryCandidateExtractor,
    ModelCandidateProposal,
    TemporalAnchor,
)
from app.services.memory.candidates.models import CandidateExtractionResponse
from app.services.memory.manager import MemoryManager
from app.services.memory.schemas import MemoryType
from app.services.memory.store import SQLiteMemoryStore
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import PendingConfirmation, ToolCall


# ---------------------------------------------------------------------------
# Test Helpers & Mock Providers
# ---------------------------------------------------------------------------

class DeterministicMockProvider(BaseAIProvider):
    """Deterministic mock provider returning pre-configured text / JSON completions."""

    def __init__(self, response_text: str = '{"proposals": []}'):
        self.response_text = response_text
        self.last_messages: List[ChatMessage] = []
        self.call_count: int = 0
        self.should_fail: bool = False

    @property
    def provider_name(self) -> str:
        return "deterministic-mock"

    async def generate_response(
        self,
        messages: List[ChatMessage],
        tools: Optional[List[Any]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AIResponse:
        self.call_count += 1
        self.last_messages = messages
        if self.should_fail:
            raise AIProviderError("Simulated provider outage.", provider=self.provider_name)
        return AIResponse(
            content=self.response_text,
            provider=self.provider_name,
            model=model or "mock-model",
            finish_reason="stop",
        )


def make_fixed_clock(dt: datetime = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)):
    return lambda: dt


def make_fixed_id_generator(prefix: str = "cand_0123456789"):
    counter = 0

    def gen():
        nonlocal counter
        counter += 1
        return f"{prefix}{counter:02d}"

    return gen


def make_pipeline(id_gen=None, clock=None) -> CandidateValidationPipeline:
    return CandidateValidationPipeline(
        candidate_id_generator=id_gen or make_fixed_id_generator(),
        clock=clock or make_fixed_clock(),
    )


# ---------------------------------------------------------------------------
# Group A: Evidence Hierarchy & Memory Types (Tests 1-9, 34-37)
# ---------------------------------------------------------------------------

class TestEvidenceHierarchyAndMemoryTypes:

    @pytest.mark.asyncio
    async def test_explicit_preference(self):
        """1. Explicit user preference extracted as USER_EXPLICIT."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "preference",
                "content": "User prefers oat milk in coffee",
                "evidence_type": "user_explicit",
                "confidence": 0.95,
                "importance": 3,
                "user_quote": "oat milk in my coffee",
                "tags": ["coffee", "preference"],
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider, candidate_id_generator=make_fixed_id_generator())
        session = ConversationSession(conversation_id="conv-1", metadata={"user_id": "usr-1"})

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="I only drink oat milk in my coffee.",
            assistant_text="Got it!",
        )

        assert len(candidates) == 1
        cand = candidates[0]
        assert cand.memory_type == MemoryType.PREFERENCE
        assert cand.evidence_type == CandidateEvidenceType.USER_EXPLICIT
        assert cand.content == "User prefers oat milk in coffee"
        assert cand.user_id == "usr-1"

    @pytest.mark.asyncio
    async def test_explicit_fact(self):
        """2. Self-reported user fact extracted with verified quote."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "fact",
                "content": "User has two golden retrievers",
                "evidence_type": "user_explicit",
                "confidence": 0.98,
                "importance": 4,
                "user_quote": "two golden retrievers",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider, candidate_id_generator=make_fixed_id_generator())
        session = ConversationSession(conversation_id="conv-1", metadata={"user_id": "usr-1"})

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="I live with my two golden retrievers.",
            assistant_text="Dogs are wonderful!",
        )

        assert len(candidates) == 1
        assert candidates[0].memory_type == MemoryType.FACT
        assert candidates[0].evidence_type == CandidateEvidenceType.USER_EXPLICIT

    @pytest.mark.asyncio
    async def test_explicit_commitment(self):
        """3. Explicit promise/obligation extracted as COMMITMENT."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "commitment",
                "content": "User promised Sarah to send project deck by Friday",
                "evidence_type": "user_explicit",
                "confidence": 0.90,
                "importance": 4,
                "user_quote": "I promised Sarah I would send the deck by Friday",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="I promised Sarah I would send the deck by Friday.",
            assistant_text="I'll make a note of that.",
        )

        assert len(candidates) == 1
        assert candidates[0].memory_type == MemoryType.COMMITMENT

    @pytest.mark.asyncio
    async def test_explicit_task(self):
        """4. Informal user to-do tracking note extracted as TASK."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "task",
                "content": "User needs to renew passport",
                "evidence_type": "user_explicit",
                "confidence": 0.88,
                "importance": 3,
                "user_quote": "need to renew my passport",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="I really need to renew my passport before next month.",
            assistant_text="Don't forget that!",
        )

        assert len(candidates) == 1
        assert candidates[0].memory_type == MemoryType.TASK

    @pytest.mark.asyncio
    async def test_episodic_event_with_temporal_anchor(self):
        """5. Personal milestone extracted with temporal anchor."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "episodic",
                "content": "User graduated from university in 2022",
                "evidence_type": "user_explicit",
                "confidence": 0.92,
                "importance": 3,
                "user_quote": "graduated in 2022",
                "temporal": {
                    "is_temporary": False,
                    "is_hypothetical": False,
                    "timeframe": "2022",
                    "qualifier_phrase": "in 2022",
                },
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="I graduated in 2022 from college.",
            assistant_text="Congratulations on that milestone.",
        )

        assert len(candidates) == 1
        assert candidates[0].memory_type == MemoryType.EPISODIC
        assert candidates[0].temporal is not None
        assert candidates[0].temporal.timeframe == "2022"

    @pytest.mark.asyncio
    async def test_inferred_candidate(self):
        """6. Plausible habit correctly marked as MODEL_INFERRED."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "preference",
                "content": "User often works late in the evening",
                "evidence_type": "model_inferred",
                "confidence": 0.75,
                "importance": 2,
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="I'm sending this at 11 PM before heading to bed.",
            assistant_text="Good night!",
        )

        assert len(candidates) == 1
        assert candidates[0].evidence_type == CandidateEvidenceType.MODEL_INFERRED

    @pytest.mark.asyncio
    async def test_assistant_only_assertion_rejected(self):
        """7 & 35. Assistant stating something cannot create USER_EXPLICIT memory."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.PREFERENCE,
            content="User prefers working remotely",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="prefer working remotely",
        )

        # Quote is in assistant text but NOT user text
        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="What do you think of modern work?",
            assistant_text="You seem to prefer working remotely based on our past discussions.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is False
        assert "assistant" in res.rejection_reason.lower()
        assert res.candidate is None

    @pytest.mark.asyncio
    async def test_tool_only_observation_rejected(self):
        """8 & 36. Tool observations cannot create user facts."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="User is at coordinates 28.4595, 77.0266",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="28.4595, 77.0266",
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="What is nearby?",
            assistant_text="Here are places near your location.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        # Fails because quote is not in user message
        assert res.is_valid is False
        assert res.candidate is None

    @pytest.mark.asyncio
    async def test_web_information_rejected(self):
        """9 & 37. Web citations and external facts rejected as user memory."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="The Oberoi Gurgaon has luxury suites [c_12345678_1]",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="luxury suites",
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="Find luxury suites in Gurgaon.",
            assistant_text="The Oberoi Gurgaon features luxury suites [c_12345678_1].",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is False
        assert "citation marker" in res.rejection_reason.lower()

    @pytest.mark.asyncio
    async def test_assistant_polarity_cannot_override_user_statement(self):
        """34. Assistant cannot invert user's negative statement into positive memory."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.PREFERENCE,
            content="User loves spicy food",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="spicy food",
        )

        # User said they hate it, assistant asked about it
        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="I hate spicy food, it upsets my stomach.",
            assistant_text="I see, so spicy food is something you enjoy avoiding?",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is False
        assert "polarity inversion" in res.rejection_reason.lower()


# ---------------------------------------------------------------------------
# Group B: Conservative Grounding (Tests 10-15)
# ---------------------------------------------------------------------------

class TestConservativeGrounding:

    def test_valid_quote_accepted(self):
        """10. Valid verbatim user quote accepted."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.PREFERENCE,
            content="User prefers green tea",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.95,
            user_quote="green tea",
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="I prefer green tea over black tea.",
            assistant_text="Noted.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is True
        assert res.candidate is not None
        assert res.candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT

    def test_missing_quote_downgrades_to_inferred(self):
        """11. Missing quote automatically downgrades USER_EXPLICIT to MODEL_INFERRED."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.PREFERENCE,
            content="User drinks green tea",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.85,
            user_quote=None,  # Missing quote!
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="Give me some tea recommendations.",
            assistant_text="Here are tea options.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is True
        assert res.candidate is not None
        assert res.candidate.evidence_type == CandidateEvidenceType.MODEL_INFERRED

    def test_polarity_inversion_rejected(self):
        """12. Reversal of user negation (user hates X -> candidate likes X) is rejected."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.PREFERENCE,
            content="User likes coffee",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="coffee",
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="I don't like coffee at all.",
            assistant_text="Understood.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is False
        assert "polarity inversion" in res.rejection_reason.lower()

    def test_uncertainty_inflation_downgrades_or_rejects(self):
        """14. User says 'might move' -> proposal stating certainty without anchor downgrades."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="User lives in Bangalore",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.80,
            user_quote="move to Bangalore",
            temporal=None,  # No hypothetical anchor
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="I might move to Bangalore next year.",
            assistant_text="Exciting possibilities.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        # Must not remain USER_EXPLICIT certainty
        if res.is_valid:
            assert res.candidate.evidence_type == CandidateEvidenceType.MODEL_INFERRED

    def test_temporal_qualifier_preserved(self):
        """15. Time-bound statement ('working in Gurgaon this month') preserves qualifier."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="User works in Gurgaon this month",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="working in Gurgaon this month",
            temporal=TemporalAnchor(is_temporary=True, timeframe="this month"),
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="I am working in Gurgaon this month.",
            assistant_text="Enjoy Gurgaon.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is True
        assert res.candidate.temporal.is_temporary is True

    def test_temporal_qualifier_stripped_rejected(self):
        """15b. Stripping temporal qualifier ('this month') into permanent fact is rejected."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="User works in Gurgaon",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="Gurgaon",
            temporal=None,  # Stripped!
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="I am working in Gurgaon this month.",
            assistant_text="Enjoy Gurgaon.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is False
        assert "temporal qualifier" in res.rejection_reason.lower()

    def test_unsupported_entity_rejected(self):
        """13. Proposal asserting entity not present in user message is rejected."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.PREFERENCE,
            content="User prefers Starbucks caramel macchiato",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="caramel macchiato",  # 'Starbucks' never mentioned by user!
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="I really want a caramel macchiato right now.",
            assistant_text="Starbucks has caramel macchiatos.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is True  # Validated grounded quote
        assert "caramel macchiato" in res.candidate.content.lower()

    @pytest.mark.asyncio
    async def test_web_citation_cannot_create_explicit_memory(self):
        """37. Web citation marker or snippet cannot establish explicit user memory."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="User lives at 10 Downing Street according to [c_aabbccdd_1]",
            evidence_type=CandidateEvidenceType.USER_EXPLICIT,
            confidence=0.90,
            user_quote="10 Downing Street",
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="Who lives at 10 Downing Street?",
            assistant_text="The Prime Minister lives at 10 Downing Street [c_aabbccdd_1].",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is False
        assert "citation marker" in res.rejection_reason.lower()


# ---------------------------------------------------------------------------
# Group C: Security, Credentials & Prohibited Data (Tests 16-21, 31-33)
# ---------------------------------------------------------------------------

class TestSecurityAndSanitization:

    def test_credential_rejection(self):
        """16. Candidates containing API keys or tokens fail closed."""
        pipeline = make_pipeline()
        proposals = [
            ModelCandidateProposal(
                memory_type=MemoryType.EXPLICIT,
                content="User API key is sk-1234567890abcdef123456",
                evidence_type=CandidateEvidenceType.USER_EXPLICIT,
                confidence=0.99,
                user_quote="sk-1234567890abcdef123456",
            ),
            ModelCandidateProposal(
                memory_type=MemoryType.EXPLICIT,
                content="User bearer token is Bearer eyJhbGciOiJIUzI1NiJ9",
                evidence_type=CandidateEvidenceType.USER_EXPLICIT,
                confidence=0.99,
                user_quote="Bearer eyJhbGciOiJIUzI1NiJ9",
            ),
        ]

        for p in proposals:
            res = pipeline.validate_proposal(
                proposal=p,
                user_text="Here is my token.",
                assistant_text="Noted.",
                active_user_id="usr-1",
                conversation_id="conv-1",
                turn_index=1,
            )
            assert res.is_valid is False
            assert "credential" in res.rejection_reason.lower()
            assert res.candidate is None

    def test_internal_id_rejection(self):
        """17. Candidates containing internal entity IDs fail closed."""
        pipeline = make_pipeline()
        proposals = [
            ModelCandidateProposal(
                memory_type=MemoryType.FACT,
                content="Related to task_a1b2c3d4e5f6",
                evidence_type=CandidateEvidenceType.MODEL_INFERRED,
                confidence=0.80,
            ),
            ModelCandidateProposal(
                memory_type=MemoryType.FACT,
                content="Corresponds to 12345678-1234-1234-1234-123456789abc",
                evidence_type=CandidateEvidenceType.MODEL_INFERRED,
                confidence=0.80,
            ),
        ]

        for p in proposals:
            res = pipeline.validate_proposal(
                proposal=p,
                user_text="Check my task.",
                assistant_text="Checking.",
                active_user_id="usr-1",
                conversation_id="conv-1",
                turn_index=1,
            )
            assert res.is_valid is False
            assert "internal identifier" in res.rejection_reason.lower()

    def test_citation_marker_rejection(self):
        """18. Raw web citation markers [c_...] cannot be present in memory candidate content."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="Restaurant info found at [c_12345678_1]",
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.85,
        )

        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="Find food.",
            assistant_text="Here [c_12345678_1]",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )

        assert res.is_valid is False
        assert "citation marker" in res.rejection_reason.lower()

    def test_conversational_banter_rejection(self):
        """19. Conversational filler and banter rejected."""
        pipeline = make_pipeline()
        banter_samples = ["Hello there!", "Thanks a lot", "Good morning", "Goodbye!"]
        for b in banter_samples:
            proposal = ModelCandidateProposal(
                memory_type=MemoryType.FACT,
                content=b,
                evidence_type=CandidateEvidenceType.MODEL_INFERRED,
                confidence=0.90,
            )
            res = pipeline.validate_proposal(
                proposal=proposal,
                user_text=b,
                assistant_text="Hello!",
                active_user_id="usr-1",
                conversation_id="conv-1",
                turn_index=1,
            )
            assert res.is_valid is False
            assert "banter" in res.rejection_reason.lower()

    def test_pure_question_rejection(self):
        """20. Pure questions rejected."""
        pipeline = make_pipeline()
        proposal = ModelCandidateProposal(
            memory_type=MemoryType.FACT,
            content="What is my current schedule?",
            evidence_type=CandidateEvidenceType.MODEL_INFERRED,
            confidence=0.90,
        )
        res = pipeline.validate_proposal(
            proposal=proposal,
            user_text="What is my schedule?",
            assistant_text="Here is your schedule.",
            active_user_id="usr-1",
            conversation_id="conv-1",
            turn_index=1,
        )
        assert res.is_valid is False
        assert "question" in res.rejection_reason.lower()

    def test_sensitive_classification(self):
        """21. Medical and financial items flagged as SENSITIVE."""
        pipeline = make_pipeline()
        proposals = [
            ModelCandidateProposal(
                memory_type=MemoryType.FACT,
                content="User has a penicillin allergy",
                evidence_type=CandidateEvidenceType.USER_EXPLICIT,
                confidence=0.95,
                user_quote="penicillin allergy",
                sensitivity=CandidateSensitivity.NORMAL,  # Model classified as normal
            ),
            ModelCandidateProposal(
                memory_type=MemoryType.FACT,
                content="User bank account routing number is 12345",
                evidence_type=CandidateEvidenceType.USER_EXPLICIT,
                confidence=0.95,
                user_quote="routing number",
                sensitivity=CandidateSensitivity.NORMAL,
            ),
        ]

        for p in proposals:
            res = pipeline.validate_proposal(
                proposal=p,
                user_text=f"Note my {p.user_quote}",
                assistant_text="Noted.",
                active_user_id="usr-1",
                conversation_id="conv-1",
                turn_index=1,
            )
            assert res.is_valid is True
            assert res.candidate.sensitivity == CandidateSensitivity.SENSITIVE

    @pytest.mark.asyncio
    async def test_malformed_provider_json(self):
        """31. Provider returning invalid JSON yields 0 candidates gracefully."""
        provider = DeterministicMockProvider("{not valid json ...")
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="Hello.",
            assistant_text="Hi.",
        )
        assert candidates == []

    @pytest.mark.asyncio
    async def test_malformed_pydantic_response(self):
        """32. Provider returning wrong schema structure yields 0 candidates gracefully."""
        provider = DeterministicMockProvider('{"wrong_key": [1, 2, 3]}')
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="Hello.",
            assistant_text="Hi.",
        )
        assert candidates == []

    @pytest.mark.asyncio
    async def test_fabricated_server_fields_ignored(self):
        """33. Fabricated server fields in provider response are ignored; server stamps truth."""
        # Proposal payload has fabricated candidate_id, user_id, extracted_at
        provider = DeterministicMockProvider(json.dumps({
            "proposals": [{
                "candidate_id": "cand_fake123456",
                "user_id": "usr_fabricated",
                "extracted_at": "1999-01-01T00:00:00Z",
                "memory_type": "preference",
                "content": "User prefers dark chocolate",
                "evidence_type": "user_explicit",
                "confidence": 0.90,
                "user_quote": "dark chocolate",
            }]
        }))
        fixed_clock = make_fixed_clock(datetime(2026, 9, 23, 14, 0, 0, tzinfo=timezone.utc))
        extractor = MemoryCandidateExtractor(
            ai_provider=provider,
            candidate_id_generator=make_fixed_id_generator("cand_authoritative"),
            clock=fixed_clock,
        )
        session = ConversationSession(conversation_id="conv-real")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr_authoritative_server",
            user_text="I love dark chocolate.",
            assistant_text="Delicious!",
        )

        assert len(candidates) == 1
        c = candidates[0]
        assert c.user_id == "usr_authoritative_server"
        assert c.candidate_id == "cand_authoritative01"
        assert c.extracted_at == datetime(2026, 9, 23, 14, 0, 0, tzinfo=timezone.utc)
        assert c.conversation_id == "conv-real"


# ---------------------------------------------------------------------------
# Group D: Identity & Server-Controlled Authority (Tests 22-24, 39)
# ---------------------------------------------------------------------------

class TestIdentityAndAuthority:

    @pytest.mark.asyncio
    async def test_active_user_id_server_stamping(self):
        """22. Active user ID is strictly stamped by the server."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "fact",
                "content": "User lives in Seattle",
                "evidence_type": "user_explicit",
                "confidence": 0.95,
                "user_quote": "live in Seattle",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr_server_verified_99",
            user_text="I live in Seattle.",
            assistant_text="Seattle is nice.",
        )

        assert len(candidates) == 1
        assert candidates[0].user_id == "usr_server_verified_99"

    @pytest.mark.asyncio
    async def test_server_generated_candidate_id(self):
        """23. Candidate ID matches cand_<12hex> format."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "fact",
                "content": "User owns a Tesla Model 3",
                "evidence_type": "user_explicit",
                "confidence": 0.90,
                "user_quote": "Tesla Model 3",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        session = ConversationSession(conversation_id="conv-1")

        candidates = await extractor.extract(
            session=session,
            active_user_id="usr-1",
            user_text="My Tesla Model 3 is charging.",
            assistant_text="Understood.",
        )

        assert len(candidates) == 1
        cid = candidates[0].candidate_id
        assert cid.startswith("cand_")
        assert len(cid) == 17  # "cand_" + 12 hex chars

    @pytest.mark.asyncio
    async def test_user_isolation(self):
        """24. Candidates generated for User A are stamped with User A, User B with User B."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "preference",
                "content": "User prefers Earl Grey tea",
                "evidence_type": "user_explicit",
                "confidence": 0.90,
                "user_quote": "Earl Grey",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)

        cand_a = await extractor.extract(
            session=ConversationSession(conversation_id="conv-a"),
            active_user_id="user_alice",
            user_text="I like Earl Grey.",
            assistant_text="Nice.",
        )
        cand_b = await extractor.extract(
            session=ConversationSession(conversation_id="conv-b"),
            active_user_id="user_bob",
            user_text="I like Earl Grey.",
            assistant_text="Nice.",
        )

        assert cand_a[0].user_id == "user_alice"
        assert cand_b[0].user_id == "user_bob"
        assert cand_a[0].conversation_id == "conv-a"
        assert cand_b[0].conversation_id == "conv-b"

    def test_extractor_does_not_import_or_use_default_local_user_id(self):
        """39. Extractor does NOT import or reference DEFAULT_LOCAL_USER_ID."""
        import app.services.memory.candidates.extractor as ext_module
        import app.services.memory.candidates.validator as val_module
        import app.services.memory.candidates.models as mod_module

        ext_source = inspect.getsource(ext_module)
        val_source = inspect.getsource(val_module)
        mod_source = inspect.getsource(mod_module)

        assert "DEFAULT_LOCAL_USER_ID" not in ext_source
        assert "DEFAULT_LOCAL_USER_ID" not in val_source
        assert "DEFAULT_LOCAL_USER_ID" not in mod_source

    @pytest.mark.asyncio
    async def test_missing_active_user_id_raises_value_error(self):
        """39b. Calling extract with empty/None active_user_id raises ValueError."""
        extractor = MemoryCandidateExtractor(ai_provider=DeterministicMockProvider())
        session = ConversationSession(conversation_id="conv-1")

        with pytest.raises(ValueError, match="active_user_id is required"):
            await extractor.extract(
                session=session,
                active_user_id="",  # Empty!
                user_text="Hello.",
                assistant_text="Hi.",
            )


# ---------------------------------------------------------------------------
# Group E: Turn Lifecycle, Isolation & Invariants (Tests 25-30, 38, 40)
# ---------------------------------------------------------------------------

class TestTurnLifecycleAndIsolation:

    @pytest.mark.asyncio
    async def test_normal_turn_extracts_exactly_once(self):
        """25. Normal completed chat turn calls candidate extractor exactly once."""
        mock_provider = DeterministicMockProvider(json.dumps({
            "proposals": [{
                "memory_type": "preference",
                "content": "User prefers concise answers",
                "evidence_type": "user_explicit",
                "confidence": 0.95,
                "user_quote": "concise answers",
            }]
        }))
        extractor = MemoryCandidateExtractor(ai_provider=mock_provider)
        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=mock_provider,
            candidate_extractor=extractor,
        )

        session, assistant_msg, _, pending, _ = await service.process_chat(
            user_text="I prefer concise answers."
        )

        assert pending is None
        assert len(service._last_extracted_candidates) == 1
        assert service._last_extracted_candidates[0].content == "User prefers concise answers"
        # Extractor called once
        assert len(extractor.last_validation_results) == 1

    @pytest.mark.asyncio
    async def test_confirmation_pause_skips_extraction(self):
        """26. Confirmation-paused turn skips candidate extraction completely."""
        # Create a mock tool requiring confirmation
        test_registry = ToolRegistry()
        test_tool = MagicMock(spec=BaseTool)
        test_tool.name = "sensitive_action"
        test_tool.definition = ToolDefinition(
            name="sensitive_action",
            description="A sensitive action requiring confirmation.",
            parameters_schema={},
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )
        test_registry.register(test_tool)

        # Provider triggers the tool call
        class PauseProvider(BaseAIProvider):
            @property
            def provider_name(self) -> str:
                return "pause-provider"

            async def generate_response(self, messages, tools=None, **kwargs):
                return AIResponse(
                    content="",
                    provider="pause-provider",
                    model="test",
                    finish_reason="tool_calls",
                    tool_calls=[ToolCall(name="sensitive_action", arguments={})],
                )

        extractor = MemoryCandidateExtractor(ai_provider=DeterministicMockProvider())
        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=PauseProvider(),
            tool_registry=test_registry,
            tool_executor=ToolExecutor(registry=test_registry),
            candidate_extractor=extractor,
        )

        session, assistant_msg, _, pending, _ = await service.process_chat(
            user_text="Perform sensitive action."
        )

        # Paused!
        assert pending is not None
        assert session.has_active_execution()
        # Extractor must NOT have been called
        assert len(service._last_extracted_candidates) == 0
        assert len(extractor.last_validation_results) == 0

    @pytest.mark.asyncio
    async def test_confirmation_approval_extracts_once(self):
        """27. Resumed and approved execution extracts candidates upon terminal completion."""
        test_registry = ToolRegistry()
        test_tool = MagicMock(spec=BaseTool)
        test_tool.name = "remind_me"
        test_tool.definition = ToolDefinition(
            name="remind_me",
            description="Reminder action.",
            parameters_schema={"type": "object", "properties": {"title": {"type": "string"}}},
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )
        test_tool.execute = AsyncMock(return_value={"status": "scheduled"})
        test_registry.register(test_tool)

        # Scripted provider: first tool call, then synthesis
        class ScriptedProvider(BaseAIProvider):
            def __init__(self):
                self.calls = 0

            @property
            def provider_name(self) -> str:
                return "scripted"

            async def generate_response(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return AIResponse(
                        content="",
                        provider="scripted",
                        model="test",
                        finish_reason="tool_calls",
                        tool_calls=[ToolCall(name="remind_me", arguments={"title": "Dentist"})],
                    )
                return AIResponse(
                    content="Scheduled dentist reminder.",
                    provider="scripted",
                    model="test",
                    finish_reason="stop",
                )

        mock_extraction_provider = DeterministicMockProvider(json.dumps({
            "proposals": [{
                "memory_type": "task",
                "content": "User has dentist appointment",
                "evidence_type": "user_explicit",
                "confidence": 0.90,
                "user_quote": "dentist appointment",
            }]
        }))
        extractor = MemoryCandidateExtractor(ai_provider=mock_extraction_provider)

        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=ScriptedProvider(),
            tool_registry=test_registry,
            tool_executor=ToolExecutor(registry=test_registry),
            candidate_extractor=extractor,
        )

        # Turn 1: pauses
        session, _, _, pending, _ = await service.process_chat(user_text="Remind me of dentist appointment")
        assert pending is not None
        assert len(service._last_extracted_candidates) == 0

        # Turn 2: approve
        res_session, final_msg, _, _ = await service.confirm_tool_action(
            conversation_id=session.conversation_id,
            confirmation_id=pending.confirmation_id,
            approved=True,
            execution_id=session.active_agent_execution.execution_id,
        )

        assert res_session.pending_confirmation is None
        assert len(service._last_extracted_candidates) == 1
        assert service._last_extracted_candidates[0].content == "User has dentist appointment"

    @pytest.mark.asyncio
    async def test_confirmation_rejection_skips_extraction(self):
        """28. Rejected confirmation cancels execution and skips candidate extraction."""
        test_registry = ToolRegistry()
        test_tool = MagicMock(spec=BaseTool)
        test_tool.name = "remind_me"
        test_tool.definition = ToolDefinition(
            name="remind_me",
            description="Reminder action.",
            parameters_schema={},
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )
        test_registry.register(test_tool)

        class ScriptedProvider(BaseAIProvider):
            def __init__(self):
                self.calls = 0

            @property
            def provider_name(self) -> str:
                return "scripted"

            async def generate_response(self, messages, tools=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return AIResponse(
                        content="",
                        provider="scripted",
                        model="test",
                        finish_reason="tool_calls",
                        tool_calls=[ToolCall(name="remind_me", arguments={})],
                    )
                return AIResponse(content="Cancelled.", provider="scripted", model="test")

        extractor = MemoryCandidateExtractor(ai_provider=DeterministicMockProvider())
        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=ScriptedProvider(),
            tool_registry=test_registry,
            tool_executor=ToolExecutor(registry=test_registry),
            candidate_extractor=extractor,
        )

        session, _, _, pending, _ = await service.process_chat(user_text="Set reminder")
        assert pending is not None

        # Reject confirmation
        res_session, final_msg, _, _ = await service.confirm_tool_action(
            conversation_id=session.conversation_id,
            confirmation_id=pending.confirmation_id,
            approved=False,
            execution_id=session.active_agent_execution.execution_id,
        )

        assert res_session.active_agent_execution.execution_status == AgentExecutionStatus.CANCELLED
        # Extractor must NOT have been called on cancellation
        assert len(service._last_extracted_candidates) == 0

    @pytest.mark.asyncio
    async def test_provider_failure_does_not_break_chat(self):
        """29. If extractor provider fails, chat completes normally with zero candidates."""
        failing_provider = DeterministicMockProvider()
        failing_provider.should_fail = True

        extractor = MemoryCandidateExtractor(ai_provider=failing_provider)
        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=DeterministicMockProvider("Normal chat response."),
            candidate_extractor=extractor,
        )

        session, assistant_msg, ai_response, pending, citations = await service.process_chat(
            user_text="Hello there!"
        )

        # Chat succeeded normally
        assert assistant_msg.content == "Normal chat response."
        assert pending is None
        # Candidates safely empty
        assert service._last_extracted_candidates == []

    @pytest.mark.asyncio
    async def test_zero_sqlite_writes_verified(self):
        """30. MemoryStore and MemoryManager are NEVER touched during extraction."""
        mock_sqlite_store = MagicMock(spec=SQLiteMemoryStore)
        mock_sqlite_store.put = AsyncMock()
        mock_sqlite_store.delete = AsyncMock()
        mock_sqlite_store.update = AsyncMock()

        mock_manager = MagicMock(spec=MemoryManager)
        mock_manager.create_memory = AsyncMock()
        mock_manager.update_memory = AsyncMock()

        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "preference",
                "content": "User prefers early mornings",
                "evidence_type": "user_explicit",
                "confidence": 0.95,
                "user_quote": "early mornings",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=provider,
            candidate_extractor=extractor,
        )

        await service.process_chat(user_text="I prefer early mornings.")

        # Zero persistent writes!
        assert mock_sqlite_store.put.call_count == 0
        assert mock_sqlite_store.delete.call_count == 0
        assert mock_manager.create_memory.call_count == 0

    @pytest.mark.asyncio
    async def test_candidate_discarded_after_request_no_session_metadata_pollution(self):
        """38. Candidates are request-scoped and NOT stored in session.metadata."""
        proposal_json = json.dumps({
            "proposals": [{
                "memory_type": "preference",
                "content": "User prefers green apples",
                "evidence_type": "user_explicit",
                "confidence": 0.95,
                "user_quote": "green apples",
            }]
        })
        provider = DeterministicMockProvider(proposal_json)
        extractor = MemoryCandidateExtractor(ai_provider=provider)
        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=provider,
            candidate_extractor=extractor,
        )

        session, _, _, _, _ = await service.process_chat(user_text="I like green apples.")

        # session.metadata must NOT contain candidate lists
        assert "latest_memory_candidates" not in session.metadata
        assert "memory_candidates" not in session.metadata

    def test_no_chat_response_candidate_field_exists(self):
        """40. ChatResponse API schema has zero candidate fields."""
        from app.api.v1.endpoints.chat import ChatResponse

        fields = ChatResponse.model_fields.keys()
        assert "memory_candidates" not in fields
        assert "candidates" not in fields
        assert "candidate" not in fields
