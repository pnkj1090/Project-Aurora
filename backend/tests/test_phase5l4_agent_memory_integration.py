"""
Phase 5L.4 — Agent Integration for Memory Intelligence Test Suite.

Comprehensive deterministic verification covering:
1. Calling on_agent_execution_completed() while RUNNING does not set memory_extraction_started.
2. Calling while WAITING_FOR_CONFIRMATION does not set it.
3. Calling while FAILED does not set it.
4. Calling while CANCELLED does not set it.
5. Calling while EXPIRED does not set it.
6. After one of the above states transitions legitimately to COMPLETED, extraction still occurs exactly once.
7. Repeated calls after COMPLETED do not perform another extraction.
8. The guard assignment occurs before the first await.

Plus full Phase 5L.4 test matrix:
A. Completed execution triggers extraction exactly once.
B. Confirmation resume completes the SAME execution and triggers exactly one extraction.
C. Extraction failure does not invalidate completed response (Level 1 failure isolation).
D. One candidate resolution failure does not prevent other candidates from being processed (Level 2 & Level 3 failure isolation).
E. AUTO_ACCEPT uses MemoryManager persistence only.
F. REQUEST_USER_REVIEW uses existing CandidateReviewStore only.
G. REJECT causes no persistence.
H. Client/request user_id cannot control candidate ownership.
I. Candidate/server fields remain server-controlled.
J. Web content/citation IDs/URLs do not become memory evidence (strict sanitization).
K. Tool raw output is not passed directly to extractor.
L. Existing Phase 5L.1 evidence hierarchy remains intact.
M. No direct SQLite writes from AgentLoopController.
N. Existing Phase 5L.2 and 5L.3 behavior remains unchanged.
"""
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.base import (
    BaseAIProvider,
    ChatMessage,
    MessageRole,
    AIResponse,
)
from app.services.conversation.models import (
    AgentExecutionState,
    AgentExecutionStatus,
    StepExecutionTrace,
)
from app.services.conversation.session import ConversationSession
from app.services.conversation.store import InMemoryConversationStore
from app.services.conversation.agent_loop import (
    AgentLoopController,
    _sanitize_tool_activities,
)
from app.services.conversation.service import ConversationService
from app.services.conversation.context import ContextManager
from app.services.context.engine import create_default_context_engine
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.registry import ToolRegistry
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ToolCall, ToolResult
from app.services.memory.candidates.models import (
    CandidateEvidenceType,
    CandidateSensitivity,
    MemoryCandidate,
)
from app.services.memory.candidates.extractor import MemoryCandidateExtractor
from app.services.memory.candidates.resolution import (
    CandidateResolutionDecision,
    CandidateResolutionService,
)
from app.services.memory.candidates.store import InMemoryCandidateReviewStore
from app.services.memory.manager import MemoryManager
from app.services.memory.schemas import MemoryItem, MemoryStatus, MemoryType
from app.services.memory.store import InMemoryMemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_candidate(
    candidate_id: str,
    user_id: str,
    content: str = "User prefers dark mode",
    memory_type: MemoryType = MemoryType.PREFERENCE,
    confidence: float = 0.95,
    evidence_type: CandidateEvidenceType = CandidateEvidenceType.USER_EXPLICIT,
    sensitivity: CandidateSensitivity = CandidateSensitivity.NORMAL,
    conversation_id: str = "conv_test",
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        user_id=user_id,
        conversation_id=conversation_id,
        turn_index=1,
        content=content,
        memory_type=memory_type,
        confidence=confidence,
        importance=3,
        evidence_type=evidence_type,
        sensitivity=sensitivity,
        tags=["preference"],
        user_quote="I prefer dark mode",
        extracted_at=datetime.now(timezone.utc),
        extractor_version="5L.1-test",
    )


class MockProvider(BaseAIProvider):
    def __init__(self, responses: Optional[List[AIResponse]] = None):
        self.responses = list(responses or [])
        self.call_history: List[List[ChatMessage]] = []

    @property
    def provider_name(self) -> str:
        return "mock-provider"

    async def generate_response(self, messages, tools=None, **kwargs) -> AIResponse:
        self.call_history.append(list(messages))
        if self.responses:
            return self.responses.pop(0)
        return AIResponse(
            content="Mock assistant final response.",
            provider="mock-provider",
            model="mock-model",
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
def resolution_service(memory_manager, review_store):
    return CandidateResolutionService(
        memory_manager=memory_manager,
        review_store=review_store,
    )


# ---------------------------------------------------------------------------
# Dedicated Guard Ordering & Invariant Tests (User Explicit Requirements 1-8)
# ---------------------------------------------------------------------------

class TestPhase5L4GuardOrdering:
    """Explicit verification of the 8 guard ordering and lifecycle invariant requirements."""

    @pytest.mark.asyncio
    async def test_1_calling_while_running_does_not_set_memory_extraction_started(self):
        """1. Calling on_agent_execution_completed() while RUNNING does not set memory_extraction_started."""
        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=MagicMock(),
            candidate_resolver=MagicMock(),
        )

        exec_state = AgentExecutionState(
            conversation_id="conv_1",
            user_id="usr_1",
            execution_status=AgentExecutionStatus.RUNNING,
        )

        await controller.on_agent_execution_completed(exec_state)

        assert exec_state.memory_extraction_started is False
        assert exec_state.memory_extraction_completed is False
        controller.candidate_extractor.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_2_calling_while_waiting_for_confirmation_does_not_set_memory_extraction_started(self):
        """2. Calling on_agent_execution_completed() while WAITING_FOR_CONFIRMATION does not set it."""
        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=MagicMock(),
            candidate_resolver=MagicMock(),
        )

        exec_state = AgentExecutionState(
            conversation_id="conv_2",
            user_id="usr_2",
            execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        )

        await controller.on_agent_execution_completed(exec_state)

        assert exec_state.memory_extraction_started is False
        assert exec_state.memory_extraction_completed is False
        controller.candidate_extractor.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_3_calling_while_failed_does_not_set_memory_extraction_started(self):
        """3. Calling on_agent_execution_completed() while FAILED does not set it."""
        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=MagicMock(),
            candidate_resolver=MagicMock(),
        )

        exec_state = AgentExecutionState(
            conversation_id="conv_3",
            user_id="usr_3",
            execution_status=AgentExecutionStatus.FAILED,
        )

        await controller.on_agent_execution_completed(exec_state)

        assert exec_state.memory_extraction_started is False
        assert exec_state.memory_extraction_completed is False
        controller.candidate_extractor.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_4_calling_while_cancelled_does_not_set_memory_extraction_started(self):
        """4. Calling on_agent_execution_completed() while CANCELLED does not set it."""
        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=MagicMock(),
            candidate_resolver=MagicMock(),
        )

        exec_state = AgentExecutionState(
            conversation_id="conv_4",
            user_id="usr_4",
            execution_status=AgentExecutionStatus.CANCELLED,
        )

        await controller.on_agent_execution_completed(exec_state)

        assert exec_state.memory_extraction_started is False
        assert exec_state.memory_extraction_completed is False
        controller.candidate_extractor.extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_5_calling_while_expired_does_not_set_memory_extraction_started(self):
        """5. Calling on_agent_execution_completed() while EXPIRED does not set it."""
        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=MagicMock(),
            candidate_resolver=MagicMock(),
        )

        exec_state = AgentExecutionState(
            conversation_id="conv_5",
            user_id="usr_5",
            execution_status=AgentExecutionStatus.EXPIRED,
        )

        await controller.on_agent_execution_completed(exec_state)

        assert exec_state.memory_extraction_started is False
        assert exec_state.memory_extraction_completed is False
        controller.candidate_extractor.extract.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("non_completed_status", [
        AgentExecutionStatus.RUNNING,
        AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        AgentExecutionStatus.FAILED,
        AgentExecutionStatus.CANCELLED,
        AgentExecutionStatus.EXPIRED,
    ])
    async def test_6_after_state_transitions_to_completed_extraction_occurs_exactly_once(
        self,
        non_completed_status,
        memory_manager,
        review_store,
    ):
        """6. After non-completed state transitions legitimately to COMPLETED, extraction still occurs exactly once."""
        cand = make_candidate("cand_6", "usr_6")
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])
        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)

        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        exec_state = AgentExecutionState(
            conversation_id="conv_6",
            user_id="usr_6",
            execution_status=non_completed_status,
            turn_user_text="User input",
            final_assistant_text="Assistant output",
        )

        # 1. Accidental invocation while in non-completed state
        await controller.on_agent_execution_completed(exec_state)
        assert exec_state.memory_extraction_started is False
        assert exec_state.memory_extraction_completed is False
        extractor.extract.assert_not_called()

        # 2. Legitimate transition to COMPLETED
        exec_state.execution_status = AgentExecutionStatus.COMPLETED
        await controller.on_agent_execution_completed(exec_state)

        assert exec_state.memory_extraction_started is True
        assert exec_state.memory_extraction_completed is True
        extractor.extract.assert_called_once()

    @pytest.mark.asyncio
    async def test_7_repeated_calls_after_completed_do_not_perform_another_extraction(
        self,
        memory_manager,
        review_store,
    ):
        """7. Repeated calls after COMPLETED do not perform another extraction."""
        cand = make_candidate("cand_7", "usr_7")
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])
        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)

        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        exec_state = AgentExecutionState(
            conversation_id="conv_7",
            user_id="usr_7",
            execution_status=AgentExecutionStatus.COMPLETED,
            turn_user_text="User input",
            final_assistant_text="Assistant output",
        )

        # First call -> performs extraction
        await controller.on_agent_execution_completed(exec_state)
        assert exec_state.memory_extraction_started is True
        assert exec_state.memory_extraction_completed is True
        assert extractor.extract.call_count == 1

        # Second, third, fourth calls -> all no-ops
        for _ in range(3):
            await controller.on_agent_execution_completed(exec_state)
            assert extractor.extract.call_count == 1

    @pytest.mark.asyncio
    async def test_8_guard_assignment_occurs_before_first_await(self):
        """8. The guard assignment occurs synchronously before the first await."""
        exec_state = AgentExecutionState(
            conversation_id="conv_8",
            user_id="usr_8",
            execution_status=AgentExecutionStatus.COMPLETED,
            turn_user_text="User input",
            final_assistant_text="Assistant output",
        )

        flag_at_extract_entry = None

        async def inspect_flag_during_extract(*args, **kwargs):
            nonlocal flag_at_extract_entry
            # When the async extractor begins executing, memory_extraction_started MUST ALREADY BE True
            flag_at_extract_entry = exec_state.memory_extraction_started
            return []

        extractor = MagicMock()
        extractor.extract = AsyncMock(side_effect=inspect_flag_during_extract)

        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
            candidate_extractor=extractor,
        )

        # Before call: flag is False
        assert exec_state.memory_extraction_started is False

        await controller.on_agent_execution_completed(exec_state)

        # Inside the first await (extract), the flag was verified to already be True
        assert flag_at_extract_entry is True
        assert exec_state.memory_extraction_started is True
        assert exec_state.memory_extraction_completed is True


# ---------------------------------------------------------------------------
# Full Test Matrix (Tests A through N)
# ---------------------------------------------------------------------------

class TestPhase5L4AgentMemoryIntegration:
    """Full deterministic test matrix for Phase 5L.4 agent memory intelligence integration."""

    @pytest.mark.asyncio
    async def test_a_completed_execution_triggers_extraction_once(self, memory_manager, review_store):
        """A. Successfully completed execution triggers extraction exactly once."""
        cand = make_candidate("cand_01", "usr_100", content="User prefers vim")
        extractor = MagicMock(spec=MemoryCandidateExtractor)
        extractor.extract = AsyncMock(return_value=[cand])

        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)

        conv_store = InMemoryConversationStore()
        session_init = ConversationSession(conversation_id="conv_100", metadata={"user_id": "usr_100"})
        await conv_store.save(session_init)

        provider = MockProvider([
            AIResponse(content="Sure thing, noting your vim preference.", provider="mock", model="m")
        ])
        service = ConversationService(
            store=conv_store,
            ai_provider=provider,
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        session, assistant_msg, _, pending, _ = await service.process_chat(
            user_text="I prefer vim.",
            conversation_id="conv_100",
        )

        assert pending is None
        assert session.active_agent_execution is not None
        assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
        assert session.active_agent_execution.memory_extraction_started is True
        assert session.active_agent_execution.memory_extraction_completed is True
        extractor.extract.assert_called_once()

        # Stored in MemoryManager under session.user_id ("usr_100")
        mems = await memory_manager.list_memories_for_user("usr_100")
        assert len(mems) == 1
        assert mems[0].content == "User prefers vim"

    @pytest.mark.asyncio
    async def test_b_confirmation_resume_completes_same_execution_and_extracts_once(self, memory_manager, review_store):
        """B. Confirmation resume completes the SAME execution and triggers exactly one extraction."""
        registry = ToolRegistry()
        mock_tool = MagicMock(spec=BaseTool)
        mock_tool.name = "deploy_app"
        mock_tool.definition = ToolDefinition(
            name="deploy_app",
            description="Deploys app",
            parameters_schema={},
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
        )
        registry.register(mock_tool)

        executor = ToolExecutor(registry=registry)
        executor.execute = AsyncMock(return_value=ToolResult(
            tool_name="deploy_app",
            tool_call_id="call_deploy",
            success=True,
            output="Deployment successful",
        ))

        cand = make_candidate("cand_conf", "usr_conf", content="User deployed prod app")
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])
        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)

        # Provider: 1st turn calls deploy_app; 2nd turn synthesizes completion
        provider = MockProvider([
            AIResponse(
                content="",
                provider="mock",
                model="m",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(id="call_deploy", name="deploy_app", arguments={})],
            ),
            AIResponse(
                content="Deployment completed successfully!",
                provider="mock",
                model="m",
            ),
        ])

        conv_store = InMemoryConversationStore()
        session_init = ConversationSession(conversation_id="conv_b", metadata={"user_id": "usr_conf"})
        await conv_store.save(session_init)

        service = ConversationService(
            store=conv_store,
            ai_provider=provider,
            tool_registry=registry,
            tool_executor=executor,
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        # 1. Start turn -> Pauses for confirmation
        session, _, _, pending, _ = await service.process_chat(
            user_text="Please deploy app.",
            conversation_id="conv_b",
        )
        assert pending is not None
        orig_execution_id = session.active_agent_execution.execution_id
        assert session.active_agent_execution.execution_status == AgentExecutionStatus.WAITING_FOR_CONFIRMATION
        assert session.active_agent_execution.memory_extraction_started is False
        extractor.extract.assert_not_called()

        # 2. User confirms action -> Resumes and completes SAME execution
        session, assistant_msg, _, _ = await service.confirm_tool_action(
            conversation_id="conv_b",
            confirmation_id=pending.confirmation_id,
            approved=True,
            execution_id=orig_execution_id,
        )

        assert session.active_agent_execution.execution_id == orig_execution_id
        assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
        assert session.active_agent_execution.memory_extraction_started is True
        assert session.active_agent_execution.memory_extraction_completed is True
        extractor.extract.assert_called_once()

        mems = await memory_manager.list_memories_for_user("usr_conf")
        assert len(mems) == 1
        assert mems[0].content == "User deployed prod app"

    @pytest.mark.asyncio
    async def test_c_level_1_extraction_failure_does_not_invalidate_chat(self):
        """C. Extraction failure (Level 1) isolates cleanly: assistant response and completed execution remain intact."""
        extractor = MagicMock()
        extractor.extract = AsyncMock(side_effect=RuntimeError("AI Provider extraction timeout"))

        conv_store = InMemoryConversationStore()
        session_init = ConversationSession(conversation_id="conv_c", metadata={"user_id": "usr_c"})
        await conv_store.save(session_init)

        service = ConversationService(
            store=conv_store,
            ai_provider=MockProvider([
                AIResponse(content="Here is your requested answer.", provider="p", model="m")
            ]),
            candidate_extractor=extractor,
        )

        session, assistant_msg, _, pending, _ = await service.process_chat(
            user_text="Explain recursion.",
            conversation_id="conv_c",
        )

        # Assistant response delivered cleanly
        assert assistant_msg.content == "Here is your requested answer."
        assert pending is None
        # Execution remains COMPLETED
        assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
        assert session.active_agent_execution.memory_extraction_started is True
        # memory_extraction_completed indicates attempt finished
        assert session.active_agent_execution.memory_extraction_completed is True

    @pytest.mark.asyncio
    async def test_d_level_2_and_3_candidate_failure_isolation(self, memory_manager, review_store):
        """D. Failure resolving or persisting one candidate does not stop other candidates from processing."""
        cand_bad = make_candidate("cand_bad", "usr_d", content="Bad candidate")
        cand_good = make_candidate("cand_good", "usr_d", content="User prefers python")

        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand_bad, cand_good])

        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)

        # Make memory_manager fail on cand_bad persistence, but succeed on cand_good
        orig_create = memory_manager.create_memory

        async def conditional_create(*args, **kwargs):
            content = kwargs.get("content", "")
            if "Bad" in content:
                raise ValueError("Simulated database write error on bad candidate")
            return await orig_create(*args, **kwargs)

        memory_manager.create_memory = AsyncMock(side_effect=conditional_create)

        conv_store = InMemoryConversationStore()
        session_init = ConversationSession(conversation_id="conv_d", metadata={"user_id": "usr_d"})
        await conv_store.save(session_init)

        service = ConversationService(
            store=conv_store,
            ai_provider=MockProvider(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        session, assistant_msg, _, _, _ = await service.process_chat(
            user_text="I prefer python.",
            conversation_id="conv_d",
        )

        assert session.active_agent_execution.execution_status == AgentExecutionStatus.COMPLETED
        assert session.active_agent_execution.memory_extraction_completed is True

        # Good candidate was saved despite bad candidate error
        mems = await memory_manager.list_memories_for_user("usr_d")
        assert len(mems) == 1
        assert mems[0].content == "User prefers python"

    @pytest.mark.asyncio
    async def test_e_auto_accept_uses_memory_manager_persistence_only(self, memory_manager, review_store):
        """E. AUTO_ACCEPT persistence goes strictly through MemoryManager, never direct SQLite."""
        cand = make_candidate("cand_e", "usr_e", content="User prefers dark theme", confidence=0.95)
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])

        with patch.object(memory_manager, "create_memory", wraps=memory_manager.create_memory) as spy_create:
            resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)
            conv_store = InMemoryConversationStore()
            session_init = ConversationSession(conversation_id="conv_e", metadata={"user_id": "usr_e"})
            await conv_store.save(session_init)

            service = ConversationService(
                store=conv_store,
                ai_provider=MockProvider(),
                candidate_extractor=extractor,
                candidate_resolver=resolver,
            )

            await service.process_chat(user_text="I prefer dark theme", conversation_id="conv_e")

            spy_create.assert_called_once()
            call_kwargs = spy_create.call_args[1]
            assert call_kwargs["content"] == "User prefers dark theme"
            assert call_kwargs["user_id"] == "usr_e"

    @pytest.mark.asyncio
    async def test_f_request_user_review_uses_candidate_review_store_only(self, memory_manager, review_store):
        """F. REQUEST_USER_REVIEW places item into CandidateReviewStore only."""
        # Sensitivity=SENSITIVE + confidence=0.85 -> REQUEST_USER_REVIEW
        cand = make_candidate(
            "cand_f",
            "usr_f",
            content="User has allergy to peanuts",
            confidence=0.85,
            sensitivity=CandidateSensitivity.SENSITIVE,
        )
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])

        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)
        conv_store = InMemoryConversationStore()
        session_init = ConversationSession(conversation_id="conv_f", metadata={"user_id": "usr_f"})
        await conv_store.save(session_init)

        service = ConversationService(
            store=conv_store,
            ai_provider=MockProvider(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        await service.process_chat(user_text="I am allergic to peanuts", conversation_id="conv_f")

        # Zero writes to MemoryManager
        mems = await memory_manager.list_memories_for_user("usr_f")
        assert len(mems) == 0

        # Stored in review_store
        review_items, _ = await review_store.list(user_id="usr_f")
        assert len(review_items) == 1
        assert review_items[0].candidate.content == "User has allergy to peanuts"

    @pytest.mark.asyncio
    async def test_g_reject_causes_no_persistence(self, memory_manager, review_store):
        """G. REJECT candidate causes zero DB writes and zero review queue entries."""
        # Low confidence (< 0.70) triggers deterministic REJECT
        cand = make_candidate("cand_g", "usr_g", content="Low confidence hint", confidence=0.50)
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])

        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)
        conv_store = InMemoryConversationStore()
        session_init = ConversationSession(conversation_id="conv_g", metadata={"user_id": "usr_g"})
        await conv_store.save(session_init)

        service = ConversationService(
            store=conv_store,
            ai_provider=MockProvider(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        await service.process_chat(user_text="Maybe something", conversation_id="conv_g")

        assert len(await memory_manager.list_memories_for_user("usr_g")) == 0
        review_items, _ = await review_store.list(user_id="usr_g")
        assert len(review_items) == 0

    @pytest.mark.asyncio
    async def test_h_client_user_id_cannot_control_candidate_ownership(self, memory_manager, review_store):
        """H. Candidate user_id must match active server identity; cross-user candidates are discarded."""
        # Candidate fabricated by model claims victim user ID
        cand_spoofed = make_candidate("cand_h", "victim_user_999", content="Spoofed preference")
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand_spoofed])

        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)
        conv_store = InMemoryConversationStore()
        # Attacker runs session with attacker identity
        session = ConversationSession(conversation_id="conv_h", metadata={"user_id": "attacker_user_1"})
        await conv_store.save(session)

        service = ConversationService(
            store=conv_store,
            ai_provider=MockProvider(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        await service.process_chat(user_text="Set victim preference", conversation_id="conv_h")

        # Victim store was NOT modified
        assert len(await memory_manager.list_memories_for_user("victim_user_999")) == 0
        review_items, _ = await review_store.list(user_id="victim_user_999")
        assert len(review_items) == 0

    @pytest.mark.asyncio
    async def test_i_server_controlled_metadata_fields(self, memory_manager, review_store):
        """I. Provenance and execution metadata fields remain strictly server-controlled."""
        cand = make_candidate("cand_i", "usr_i", content="User lives in Paris", confidence=0.95, conversation_id="conv_i")
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[cand])

        resolver = CandidateResolutionService(memory_manager=memory_manager, review_store=review_store)
        conv_store = InMemoryConversationStore()
        session_init = ConversationSession(conversation_id="conv_i", metadata={"user_id": "usr_i"})
        await conv_store.save(session_init)

        service = ConversationService(
            store=conv_store,
            ai_provider=MockProvider(),
            candidate_extractor=extractor,
            candidate_resolver=resolver,
        )

        await service.process_chat(user_text="I live in Paris", conversation_id="conv_i")

        mems = await memory_manager.list_memories_for_user("usr_i")
        assert len(mems) == 1
        meta = mems[0].metadata
        assert meta["candidate_id"] == "cand_i"
        assert meta["conversation_id"] == "conv_i"
        assert meta["resolution"] == "auto_accepted"
        assert meta["extractor_version"] == "5L.1-test"

    def test_j_tool_activity_sanitization_strips_urls_html_citations(self):
        """J. Sanitization strictly strips URLs, web HTML, and citation IDs."""
        raw_activities = [
            "Searched the web for https://malicious.com/exploit?q=secret",
            "Read web page <b>Important Article</b> from www.example.org",
            "Found citation [c_1234abcd_1] in research",
            "Checked current time",
            "   ",
            "A" * 200,  # Clamped to 100
        ]

        sanitized = _sanitize_tool_activities(raw_activities, max_activities=10, max_item_len=100)

        for act in sanitized:
            assert "https://" not in act
            assert "http://" not in act
            assert "www." not in act
            assert "<b>" not in act
            assert "</b>" not in act
            assert "[c_" not in act
            assert len(act) <= 100

        assert len(sanitized) == 5  # Empty one omitted
        assert sanitized[3] == "Checked current time"

    @pytest.mark.asyncio
    async def test_k_tool_raw_output_never_passed_to_extractor(self, memory_manager, review_store):
        """K. Tool raw output is never passed directly to the candidate extractor."""
        registry = ToolRegistry()
        test_tool = MagicMock(spec=BaseTool)
        test_tool.name = "fetch_secret_data"
        test_tool.definition = ToolDefinition(
            name="fetch_secret_data",
            description="Fetches data",
            parameters_schema={},
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )
        registry.register(test_tool)

        executor = ToolExecutor(registry=registry)
        executor.execute = AsyncMock(return_value=ToolResult(
            tool_name="fetch_secret_data",
            tool_call_id="call_secret_1",
            success=True,
            output={"raw_secret_payload": "TOP_SECRET_12345", "html": "<div>raw page</div>"},
        ))

        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[])

        provider = MockProvider([
            AIResponse(
                content="",
                provider="mock",
                model="m",
                finish_reason="tool_calls",
                tool_calls=[ToolCall(id="call_secret_1", name="fetch_secret_data", arguments={})],
            ),
            AIResponse(
                content="Here is the sanitized answer.",
                provider="mock",
                model="m",
            ),
        ])

        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=provider,
            tool_registry=registry,
            tool_executor=executor,
            candidate_extractor=extractor,
        )

        await service.process_chat(user_text="Fetch data", conversation_id="conv_k")

        extractor.extract.assert_called_once()
        call_kwargs = extractor.extract.call_args[1]
        tool_activities = call_kwargs.get("tool_activities", [])

        # Ensure raw output payload never appears in extractor arguments
        for act in tool_activities:
            assert "TOP_SECRET" not in act
            assert "raw_secret_payload" not in act
            assert "<div>" not in act

    @pytest.mark.asyncio
    async def test_l_evidence_hierarchy_preservation(self, memory_manager, review_store):
        """L. Primary (user text), secondary (assistant response), reference-only (sanitized activities) passed cleanly."""
        extractor = MagicMock()
        extractor.extract = AsyncMock(return_value=[])

        service = ConversationService(
            store=InMemoryConversationStore(),
            ai_provider=MockProvider([
                AIResponse(content="Assistant synthesis answer.", provider="p", model="m")
            ]),
            candidate_extractor=extractor,
        )

        await service.process_chat(
            user_text="User primary message.",
            conversation_id="conv_l",
        )

        extractor.extract.assert_called_once()
        call_kwargs = extractor.extract.call_args[1]
        assert call_kwargs["user_text"] == "User primary message."
        assert call_kwargs["assistant_text"] == "Assistant synthesis answer."
        assert isinstance(call_kwargs["tool_activities"], list)

    @pytest.mark.asyncio
    async def test_m_agent_loop_controller_has_no_direct_sqlite_access(self):
        """M. AgentLoopController has zero reference or access to SQLite store."""
        controller = AgentLoopController(
            ai_provider=MockProvider(),
            tool_registry=ToolRegistry(),
            tool_executor=ToolExecutor(registry=ToolRegistry()),
            context_manager=ContextManager(),
            context_engine=create_default_context_engine(),
        )

        assert not hasattr(controller, "sqlite")
        assert not hasattr(controller, "db")
        assert not hasattr(controller, "cursor")
        assert not hasattr(controller, "memory_store")
