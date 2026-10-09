"""
Orchestration layer for Phase 5L.1 — Memory Candidate Extraction.
Coordinates AI provider dispatch, JSON parsing, and validation pipeline.

CRITICAL INVARIANTS:
1. Zero persistence: Never calls MemoryStore or MemoryManager.
2. Server identity stamping: active_user_id must be provided by the server.
3. Provider neutrality: Uses BaseAIProvider interface.
4. Fail-closed error isolation: Provider or parsing failures yield zero candidates cleanly.
"""
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, List, Optional

from app.services.ai.base import (
    BaseAIProvider,
    ChatMessage,
    MessageRole,
)
from app.services.memory.candidates.models import (
    CandidateExtractionResponse,
    CandidateValidationResult,
    MemoryCandidate,
)
from app.services.memory.candidates.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt,
)
from app.services.memory.candidates.validator import (
    CandidateValidationPipeline,
)

logger = logging.getLogger("aurora.memory.candidates.extractor")


class MemoryCandidateExtractor:
    """
    Analyzes completed conversation turns and produces validated MemoryCandidate objects.
    All candidates are in-memory, request-scoped, and ephemeral in Phase 5L.1.
    """

    def __init__(
        self,
        ai_provider: BaseAIProvider,
        validator: Optional[CandidateValidationPipeline] = None,
        candidate_id_generator: Optional[Callable[[], str]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.ai_provider = ai_provider
        self.validator = validator or CandidateValidationPipeline(
            candidate_id_generator=candidate_id_generator,
            clock=clock,
        )
        self._last_validation_results: List[CandidateValidationResult] = []

    @property
    def last_validation_results(self) -> List[CandidateValidationResult]:
        """Diagnostic access for testing and operational introspection."""
        return self._last_validation_results

    async def extract(
        self,
        session: Any,
        active_user_id: str,
        user_text: str,
        assistant_text: str,
        tool_activities: Optional[List[str]] = None,
        turn_index: int = 0,
        model: Optional[str] = None,
    ) -> List[MemoryCandidate]:
        """
        Executes candidate extraction for a completed turn.
        
        Args:
            session: Active ConversationSession.
            active_user_id: Authoritative user ID stamped by the server.
            user_text: User message content from this turn.
            assistant_text: Assistant synthesized response from this turn.
            tool_activities: High-level safe tool activity descriptions.
            turn_index: Monotonic turn index within the conversation.
            model: Optional model identifier.
            
        Returns:
            List of validated MemoryCandidate objects (empty list on failure or no candidates).
        """
        start_time = time.monotonic()
        self._last_validation_results = []

        # 1. Identity Validation: Server must provide active_user_id
        if not active_user_id or not str(active_user_id).strip():
            logger.error("Candidate extraction failed: active_user_id is missing or empty.")
            raise ValueError("active_user_id is required for candidate extraction.")

        clean_user = (user_text or "").strip()
        clean_asst = (assistant_text or "").strip()
        if not clean_user:
            return []

        conversation_id = getattr(session, "conversation_id", "unknown_conversation")

        # 2. Build extraction messages
        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=EXTRACTION_SYSTEM_PROMPT),
            ChatMessage(
                role=MessageRole.USER,
                content=build_extraction_user_prompt(
                    user_text=clean_user,
                    assistant_text=clean_asst,
                    tool_activities=tool_activities,
                ),
            ),
        ]

        # 3. Provider dispatch with structured JSON request
        try:
            ai_response = await self.ai_provider.generate_response(
                messages=messages,
                model=model,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning(
                "Memory candidate extraction provider call failed gracefully: error_type=%s, duration_ms=%d",
                type(exc).__name__,
                duration_ms,
            )
            return []

        raw_content = (ai_response.content or "").strip()
        if not raw_content:
            return []

        # 4. Parse structured model output
        try:
            # Handle potential markdown code block wrapping e.g. ```json ... ```
            if raw_content.startswith("```"):
                lines = raw_content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw_content = "\n".join(lines).strip()

            parsed_response = CandidateExtractionResponse.model_validate_json(raw_content)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning(
                "Memory candidate extraction JSON validation failed: error_type=%s, duration_ms=%d",
                type(exc).__name__,
                duration_ms,
            )
            return []

        proposals = parsed_response.proposals
        if not proposals:
            return []

        # 5. Deterministic validation pipeline
        results = self.validator.validate_proposals(
            proposals=proposals,
            user_text=clean_user,
            assistant_text=clean_asst,
            active_user_id=active_user_id,
            conversation_id=conversation_id,
            turn_index=turn_index,
        )
        self._last_validation_results = results

        accepted_candidates: List[MemoryCandidate] = [
            r.candidate for r in results if r.is_valid and r.candidate is not None
        ]

        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "Memory candidate extraction completed: accepted=%d, rejected=%d, duration_ms=%d",
            len(accepted_candidates),
            len(results) - len(accepted_candidates),
            duration_ms,
        )

        return accepted_candidates
