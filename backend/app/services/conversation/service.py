import json
import logging
from typing import Optional, Tuple, List

from app.services.ai.base import (
    BaseAIProvider,
    ChatMessage,
    MessageRole,
    AIResponse,
)
from app.services.ai.factory import get_ai_provider
from app.services.conversation.session import ConversationSession
from app.services.conversation.store import BaseConversationStore, default_conversation_store
from app.services.conversation.context import ContextManager
from app.services.conversation.models import (
    AgentExecutionState,
    AgentExecutionStatus,
    ActiveExecutionConflictError,
)
from app.services.conversation.agent_loop import AgentLoopController
from app.services.tools.registry import ToolRegistry, default_tool_registry
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import PendingConfirmation
from app.services.context.engine import ContextEngine, create_default_context_engine
from app.services.web.citations.models import CitationOut
from app.services.memory.candidates.extractor import MemoryCandidateExtractor
from app.services.memory.candidates.models import MemoryCandidate
from app.services.memory.candidates.resolution import CandidateResolutionService
from app.services.memory import get_memory_manager

logger = logging.getLogger("aurora.conversation.service")


class ConversationIdentityError(Exception):
    """Raised when an authenticated user does not own the requested session state."""


class ConversationService:
    """
    Core Conversation Engine coordinating session persistence, context window assembly,
    Tool Registry discovery, Tool Executor multi-step loop, and AI Gateway dispatch.
    """

    def __init__(
        self,
        store: Optional[BaseConversationStore] = None,
        ai_provider: Optional[BaseAIProvider] = None,
        context_manager: Optional[ContextManager] = None,
        tool_registry: Optional[ToolRegistry] = None,
        tool_executor: Optional[ToolExecutor] = None,
        context_engine: Optional[ContextEngine] = None,
        max_tool_iterations: int = 5,
        agent_loop: Optional[AgentLoopController] = None,
        candidate_extractor: Optional[MemoryCandidateExtractor] = None,
        extraction_provider: Optional[BaseAIProvider] = None,
        candidate_resolver: Optional[CandidateResolutionService] = None,
    ):
        self.store = store or default_conversation_store
        self.ai_provider = ai_provider or get_ai_provider()
        self.context_manager = context_manager or ContextManager()
        self.tool_registry = tool_registry or default_tool_registry
        self.tool_executor = tool_executor or ToolExecutor(registry=self.tool_registry)
        self.context_engine = context_engine or create_default_context_engine()
        self.max_tool_iterations = max_tool_iterations
        self.agent_loop = agent_loop or AgentLoopController(
            ai_provider=self.ai_provider,
            tool_registry=self.tool_registry,
            tool_executor=self.tool_executor,
            context_manager=self.context_manager,
            context_engine=self.context_engine,
            max_tool_iterations=self.max_tool_iterations,
        )
        self.candidate_extractor = candidate_extractor or MemoryCandidateExtractor(
            ai_provider=extraction_provider or get_ai_provider()
        )
        self.candidate_resolver = candidate_resolver or CandidateResolutionService(
            memory_manager=get_memory_manager()
        )
        # Wire candidate extractor and resolver into AgentLoopController
        if getattr(self.agent_loop, "candidate_extractor", None) is None:
            self.agent_loop.candidate_extractor = self.candidate_extractor
        if getattr(self.agent_loop, "candidate_resolver", None) is None:
            self.agent_loop.candidate_resolver = self.candidate_resolver

    @property
    def _last_extracted_candidates(self) -> List[MemoryCandidate]:
        return getattr(self.agent_loop, "_last_extracted_candidates", [])

    @_last_extracted_candidates.setter
    def _last_extracted_candidates(self, val: List[MemoryCandidate]) -> None:
        if hasattr(self.agent_loop, "_last_extracted_candidates"):
            self.agent_loop._last_extracted_candidates = val

    async def get_session(self, conversation_id: str) -> Optional[ConversationSession]:
        return await self.store.get(conversation_id)

    async def create_session(self, conversation_id: Optional[str] = None) -> ConversationSession:
        return await self.store.create(conversation_id)

    async def process_chat(
        self,
        user_text: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        authenticated_user_id: Optional[str] = None,
    ) -> Tuple[ConversationSession, ChatMessage, AIResponse, Optional[PendingConfirmation], List["CitationOut"]]:
        """
        Processes an incoming user message through the Multi-Step Agentic Tool Loop:
        1. Retrieves or creates session.
        2. Guards against active execution concurrency (rejects with ActiveExecutionConflictError if busy).
        3. Appends user message.
        4. Delegates to AgentLoopController (evaluates up to max_tool_iterations, whole-batch parallel preflight).
        5. Intercepts confirmation requirements or completes with tool-free synthesis.
        Returns (session, assistant_msg, ai_response, pending, citations_out).
        """
        clean_text = user_text.strip()
        if not clean_text:
            raise ValueError("Message content cannot be empty.")

        # 1. Retrieve or initialize session
        session = None
        if conversation_id:
            session = await self.store.get(conversation_id)

        if not session:
            session = await self.store.create(conversation_id)
            if authenticated_user_id:
                session.metadata["user_id"] = authenticated_user_id

        if authenticated_user_id and session.user_id != authenticated_user_id:
            raise ConversationIdentityError("Conversation session not found.")

        # 2. Concurrency guard: check if conversation has an active execution
        if session.has_active_execution():
            logger.warning(
                "Conversation '%s' has active execution (status: %s). Rejecting concurrent request.",
                session.conversation_id,
                session.active_agent_execution.execution_status.value if session.active_agent_execution else "unknown",
            )
            raise ActiveExecutionConflictError(
                "Conversation currently has an active execution in progress or waiting for confirmation. "
                "Please confirm or cancel the pending action before sending new messages."
            )

        # 3. Append user message
        session.add_message(role=MessageRole.USER, content=clean_text)

        # 4. Execute turn through AgentLoopController (handles completion and memory intelligence)
        assistant_msg, ai_response, pending, citations_out = await self.agent_loop.execute_turn(
            session=session,
            user_text=clean_text,
            model=model,
        )

        await self.store.save(session)
        return session, assistant_msg, ai_response, pending, citations_out

    async def confirm_tool_action(
        self,
        conversation_id: str,
        confirmation_id: str,
        approved: bool,
        model: Optional[str] = None,
        authenticated_user_id: Optional[str] = None,
        *,
        execution_id: str,
    ) -> Tuple[ConversationSession, ChatMessage, AIResponse, List["CitationOut"]]:
        """
        Resumes an action that was paused for user confirmation:
        - Validates multi-point correlation between execution, session, confirmation ID, and tool call.
        - If approved: executes pre-approved tool and resumes agent loop at step N+1.
        - If rejected: records cancellation and synthesizes acknowledgment.
        Returns (session, assistant_msg, ai_response, citations_out).
        """
        session = await self.store.get(conversation_id)
        if not session:
            raise ValueError(f"Conversation session '{conversation_id}' not found.")

        if authenticated_user_id and session.user_id != authenticated_user_id:
            raise ConversationIdentityError("Conversation session not found.")

        if authenticated_user_id:
            execution = session.active_agent_execution
            pending = session.pending_confirmation
            if (
                execution is None
                or pending is None
                or execution.user_id != authenticated_user_id
                or execution.conversation_id != conversation_id
                or execution.execution_id != execution_id
                or execution.pending_confirmation_id != confirmation_id
                or execution.execution_status != AgentExecutionStatus.WAITING_FOR_CONFIRMATION
                or execution.paused_tool_call is None
                or pending.confirmation_id != confirmation_id
                or pending.agent_execution_id != execution.execution_id
                or pending.tool_call.id != execution.paused_tool_call.id
            ):
                raise ConversationIdentityError("Pending confirmation not found.")

        # Ensure active_agent_execution is correlated if session has a pending confirmation
        if session.active_agent_execution is None and session.pending_confirmation is not None:
            if session.pending_confirmation.confirmation_id == confirmation_id:
                exec_state = AgentExecutionState(
                    conversation_id=session.conversation_id,
                    user_id=session.user_id,
                    execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
                    paused_tool_call=session.pending_confirmation.tool_call.model_copy(deep=True),
                    pending_confirmation_id=confirmation_id,
                )
                session.pending_confirmation.agent_execution_id = exec_state.execution_id
                session.active_agent_execution = exec_state

        # Concurrency / state validation
        if (
            not session.has_active_execution()
            or session.active_agent_execution.execution_status != AgentExecutionStatus.WAITING_FOR_CONFIRMATION
        ):
            logger.warning("No action waiting for confirmation in conversation '%s'", conversation_id)
            raise ActiveExecutionConflictError(
                f"No action is currently waiting for confirmation in conversation '{conversation_id}'."
            )

        assistant_msg, ai_response, citations_out = await self.agent_loop.resume_execution(
            session=session,
            confirmation_id=confirmation_id,
            approved=approved,
            model=model,
        )

        await self.store.save(session)
        return session, assistant_msg, ai_response, citations_out

    async def _safe_extract_memory_candidates(
        self,
        session: ConversationSession,
        user_text: str = "",
        assistant_text: str = "",
        model: Optional[str] = None,
    ) -> List[MemoryCandidate]:
        """
        Testing and diagnostic helper delegating to AgentLoopController.on_agent_execution_completed.
        """
        if session.active_agent_execution:
            if user_text:
                session.active_agent_execution.turn_user_text = user_text
            if assistant_text:
                session.active_agent_execution.final_assistant_text = assistant_text
            await self.agent_loop.on_agent_execution_completed(
                execution_state=session.active_agent_execution,
                session=session,
                model=model,
            )
        return self._last_extracted_candidates
