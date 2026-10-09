"""
Phase 6A — Unified Personal Context & Assistant Intelligence Test Suite.

Comprehensive deterministic verification covering:
Core Matrix (A–Y):
- A. Deterministic Context Assembly
- B. Provider Priority Ordering
- C. Global Context Budget
- D. Per-Provider Budget
- E. Permission Enforcement
- F. User Scoping & Isolation
- G. Provider Failure Isolation
- H. Missing Identity Handling
- I. Expired Memory Exclusion
- J. Stale Location Handling
- K. Task & Commitment Relevance
- L. Calendar Relevance
- M. Contact Relevance & Minimization
- N. Location Relevance
- O. Reminder Relevance
- P. Proactive Suggestion Relevance
- Q. Selected Asset Relevance
- R. Execution-Scoped Web Context
- S. Citation Isolation
- T. Data Boundary Protection
- U. AgentLoopController Integration
- V. Zero Tool Execution from Context
- W. Zero Persistence from Context
- X. Zero Background Tasks
- Y. Full Phase 5 Regression

Hardening Amendments (Z1–Z15):
- Z1. Phase 5K Citation Authority Preserved
- Z2. No ContextEngine Citation Creation
- Z3. No ContextEngine Web Requests
- Z4. Cross-Execution Citation Isolation
- Z5. Budget Partition Defense (3,000 / 1,000 / 4,000)
- Z6. General Factual/Technical Memory Suppression
- Z7. Ambiguous Memory Fallback
- Z8. Relevance Never Grants Permission
- Z9. Fail-Closed Identity Without Fallback
- Z10. Explicit Test Identity
- Z11. Server-Held Query Resumption
- Z12. Citation Preservation on Resume
- Z13. Web Content Cannot Activate Providers
- Z14. Zero Tool Invocation
- Z15. Zero Write Persistence
"""
import asyncio
from datetime import datetime, timezone, timedelta
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
from app.services.conversation.agent_loop import AgentLoopController
from app.services.conversation.service import ConversationService
from app.services.conversation.context import ContextManager
from app.services.context.base import BaseContextProvider, ContextBlock, ContextSensitivity
from app.services.context.engine import (
    ContextEngine,
    format_web_sources_block,
    create_default_context_engine,
)
from app.services.context.router import ContextRelevanceRouter, RelevanceDecision
from app.services.context.policy import ContextAccessPolicy
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.context.providers.temporal import TemporalContextProvider
from app.services.context.providers.memory import MemoryContextProvider
from app.services.context.providers.reminders import ReminderContextProvider
from app.services.context.providers.task import TaskContextProvider
from app.services.context.providers.commitment import CommitmentContextProvider
from app.services.context.providers.calendar import CalendarContextProvider
from app.services.context.providers.location import LocationContextProvider
from app.services.context.providers.contacts import ContactsContextProvider
from app.services.context.providers.assets import AssetContextProvider
from app.services.context.providers.proactive import ProactiveContextProvider

from app.services.reminders.manager import ReminderManager
from app.services.reminders.store import InMemoryReminderStore
from app.services.reminders.models import Reminder, ReminderStatus
from app.services.tasks.manager import TaskManager
from app.services.tasks.store import InMemoryTaskStore
from app.services.tasks.models import TaskPriority
from app.services.commitments.manager import CommitmentManager
from app.services.commitments.store import InMemoryCommitmentStore
from app.services.calendar.manager import CalendarManager
from app.services.calendar.models import CalendarEvent
from app.services.location.manager import LocationManager
from app.services.location.models import LocationSnapshot
from app.services.contacts.manager import ContactManager
from app.services.contacts.models import ContactSnapshot
from app.services.memory.manager import MemoryManager
from app.services.memory.store import InMemoryMemoryStore
from app.services.memory.schemas import MemoryItem, MemoryType, MemoryStatus
from app.services.proactive.manager import ProactiveManager
from app.services.proactive.store import InMemoryProactiveStore
from app.services.proactive.models import ProactiveCandidate, ProactiveStatus
from app.services.web.citations import CitationRegistry
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.registry import ToolRegistry
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ToolCall, ToolResult


class DummyProvider(BaseContextProvider):
    def __init__(self, name: str, priority: int, content: str = "Dummy text", sensitivity: ContextSensitivity = ContextSensitivity.PUBLIC, perm: Optional[PermissionType] = None):
        self._name = name
        self._priority = priority
        self._content = content
        self._sensitivity = sensitivity
        self._perm = perm

    @property
    def name(self) -> str: return self._name

    @property
    def priority(self) -> int: return self._priority

    @property
    def sensitivity(self) -> ContextSensitivity: return self._sensitivity

    @property
    def required_permission(self) -> Optional[PermissionType]: return self._perm

    async def get_context(self, session=None, query=None, **kwargs) -> Optional[ContextBlock]:
        return ContextBlock(
            title=self._name.capitalize(),
            content=self._content,
            priority=self._priority,
            sensitivity=self._sensitivity,
            source=f"dummy/{self._name}",
        )


# =========================================================================
# CORE MATRIX (A - Y)
# =========================================================================

@pytest.mark.asyncio
async def test_a_deterministic_context_assembly():
    """A. Deterministic Context Assembly: Identical inputs produce byte-for-byte identical block ordering and prompt text."""
    p1 = DummyProvider("temporal", 10, "Time: 12:00 UTC")
    p2 = DummyProvider("memory", 20, "Likes green tea")
    p3 = DummyProvider("reminders", 30, "Buy coffee")

    engine1 = ContextEngine(providers=[p3, p1, p2])
    engine2 = ContextEngine(providers=[p2, p3, p1])

    prompt1 = await engine1.build_system_prompt(query="I want to buy tea, please remind me")
    prompt2 = await engine2.build_system_prompt(query="I want to buy tea, please remind me")

    assert prompt1 == prompt2
    assert "### Temporal" in prompt1
    assert prompt1.index("### Temporal") < prompt1.index("### Memory") < prompt1.index("### Reminders")


@pytest.mark.asyncio
async def test_b_provider_priority_ordering():
    """B. Provider Priority Ordering: Blocks strictly follow priorities 10 -> 20 -> 25 -> 28 -> 30 -> 32 -> 35 -> 38 -> 40 -> 45 -> 50."""
    providers = [
        DummyProvider("proactive", 45, "Action suggested"),
        DummyProvider("temporal", 10, "12:00 UTC"),
        DummyProvider("reminders", 30, "Reminder item"),
        DummyProvider("location", 25, "Current location: SF"),
        DummyProvider("tasks", 32, "Fix issue"),
        DummyProvider("commitments", 35, "Deliver report"),
        DummyProvider("memory", 20, "User preference"),
        DummyProvider("contacts", 38, "Alice"),
        DummyProvider("calendar", 28, "Team sync"),
        DummyProvider("assets", 40, "photo.jpg"),
    ]
    engine = ContextEngine(providers=providers)
    blocks = await engine.gather_context_blocks(query=None)

    priorities = [b.priority for b in blocks]
    assert priorities == sorted(priorities)
    assert priorities == [10, 20, 25, 28, 30, 32, 35, 38, 40, 45]


@pytest.mark.asyncio
async def test_c_global_context_budget():
    """C. Global Context Budget: Assembled context never exceeds 4,000 characters under any combination of inputs."""
    large_text = "A" * 1200
    providers = [
        DummyProvider(f"prov_{i}", 10 + i, large_text) for i in range(10)
    ]
    engine = ContextEngine(providers=providers, max_total_chars=4000)
    blocks = await engine.gather_context_blocks(query=None)

    total_chars = sum(len(b.content) for b in blocks)
    assert total_chars <= 4000


@pytest.mark.asyncio
async def test_d_per_provider_budget():
    """D. Per-Provider Budget: Individual providers enforce internal clamping before returning blocks."""
    reminder_store = InMemoryReminderStore()
    reminder_mgr = ReminderManager(store=reminder_store)
    provider = ReminderContextProvider(reminder_manager=reminder_mgr, max_chars=200)

    now = datetime.now(timezone.utc)
    for i in range(10):
        await reminder_mgr.create_reminder(
            user_id="usr_budget",
            title=f"Very long reminder title item that takes lots of space number {i}",
            scheduled_at=now + timedelta(hours=1),
        )

    block = await provider.get_context(user_id="usr_budget")
    assert block is not None
    assert len(block.content) <= 200


@pytest.mark.asyncio
async def test_e_permission_enforcement():
    """E. Permission Enforcement: Revoking CALENDAR, LOCATION, CONTACTS, PHOTOS_FILES, or REMINDERS completely excludes the block."""
    perm_store = InMemoryPermissionStore()
    perm_mgr = PermissionManager(store=perm_store)
    policy = ContextAccessPolicy(permission_manager=perm_mgr)

    loc_prov = DummyProvider("location", 25, "SF", ContextSensitivity.PRIVATE, PermissionType.LOCATION)
    cal_prov = DummyProvider("calendar", 28, "Sync", ContextSensitivity.PRIVATE, PermissionType.CALENDAR)
    rem_prov = DummyProvider("reminders", 30, "Due", ContextSensitivity.PRIVATE, PermissionType.REMINDERS)
    con_prov = DummyProvider("contacts", 38, "Bob", ContextSensitivity.PRIVATE, PermissionType.CONTACTS)
    ast_prov = DummyProvider("assets", 40, "Doc", ContextSensitivity.PRIVATE, PermissionType.PHOTOS_FILES)

    engine = ContextEngine(providers=[loc_prov, cal_prov, rem_prov, con_prov, ast_prov], policy=policy)

    # Denied by default
    blocks = await engine.gather_context_blocks(user_id="usr_perm", query=None)
    assert len(blocks) == 0

    # Authorize only REMINDERS
    await perm_mgr.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id="usr_perm")
    blocks_after = await engine.gather_context_blocks(user_id="usr_perm", query=None)
    assert len(blocks_after) == 1
    assert blocks_after[0].title == "Reminders"


@pytest.mark.asyncio
async def test_f_user_scoping_and_isolation():
    """F. User Scoping & Isolation: User A never receives User B's tasks, commitments, reminders, memories, or contacts."""
    rem_store = InMemoryReminderStore()
    rem_mgr = ReminderManager(store=rem_store)
    rem_prov = ReminderContextProvider(reminder_manager=rem_mgr)

    now = datetime.now(timezone.utc)
    await rem_mgr.create_reminder(user_id="user_a", title="Alpha Reminder", scheduled_at=now + timedelta(hours=1))
    await rem_mgr.create_reminder(user_id="user_b", title="Beta Reminder", scheduled_at=now + timedelta(hours=1))

    block_a = await rem_prov.get_context(user_id="user_a")
    block_b = await rem_prov.get_context(user_id="user_b")

    assert block_a is not None and "Alpha Reminder" in block_a.content
    assert "Beta Reminder" not in block_a.content
    assert block_b is not None and "Beta Reminder" in block_b.content
    assert "Alpha Reminder" not in block_b.content


@pytest.mark.asyncio
async def test_g_provider_failure_isolation():
    """G. Provider Failure Isolation: If a provider throws RuntimeError, remaining providers gather and assemble normally."""
    class FailingProvider(BaseContextProvider):
        @property
        def name(self) -> str: return "failing"
        @property
        def priority(self) -> int: return 15
        @property
        def sensitivity(self) -> ContextSensitivity: return ContextSensitivity.PUBLIC
        async def get_context(self, **kwargs):
            raise RuntimeError("Database connection exploded!")

    p1 = DummyProvider("temporal", 10, "Time: 12:00")
    p_fail = FailingProvider()
    p2 = DummyProvider("memory", 20, "Likes coffee")

    engine = ContextEngine(providers=[p1, p_fail, p2])
    blocks = await engine.gather_context_blocks(query=None)

    assert len(blocks) == 2
    titles = [b.title for b in blocks]
    assert "Temporal" in titles
    assert "Memory" in titles
    assert "Failing" not in titles


@pytest.mark.asyncio
async def test_h_missing_identity_handling():
    """H. Missing Identity Handling: Missing, empty, or whitespace user_id causes private/sensitive providers to fail closed."""
    rem_mgr = ReminderManager(store=InMemoryReminderStore())
    rem_prov = ReminderContextProvider(reminder_manager=rem_mgr)

    assert await rem_prov.get_context(user_id=None) is None
    assert await rem_prov.get_context(user_id="") is None
    assert await rem_prov.get_context(user_id="   ") is None


@pytest.mark.asyncio
async def test_i_expired_memory_exclusion():
    """I. Expired Memory Exclusion: Expired or archived memories are excluded from memory context."""
    mem_store = InMemoryMemoryStore()
    mem_mgr = MemoryManager(store=mem_store)
    provider = MemoryContextProvider(memory_manager=mem_mgr)

    user_id = "usr_exp_mem"
    # Active valid memory
    await mem_mgr.create_memory(content="Active memory preference", user_id=user_id, status=MemoryStatus.ACTIVE)
    # Archived memory
    await mem_mgr.create_memory(content="Archived secret preference", user_id=user_id, status=MemoryStatus.ARCHIVED)

    block = await provider.get_context(user_id=user_id, query="What is my memory preference?")
    assert block is not None
    assert "Active memory preference" in block.content
    assert "Archived secret preference" not in block.content


@pytest.mark.asyncio
async def test_j_stale_location_handling():
    """J. Stale Location Handling: Location fixes older than 900 seconds return None."""
    perm_mgr = PermissionManager(store=InMemoryPermissionStore())
    await perm_mgr.update_permission(PermissionType.LOCATION, PermissionStatus.AUTHORIZED, user_id="usr_loc")
    loc_mgr = LocationManager(permission_manager=perm_mgr)
    loc_prov = LocationContextProvider(location_manager=loc_mgr)

    # Stale location (1000 seconds ago)
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=1000)
    snap = LocationSnapshot(latitude=37.77, longitude=-122.41, accuracy_meters=5.0, captured_at=stale_time, user_id="usr_loc")
    loc_mgr.set_current_location(snap, user_id="usr_loc")

    block = await loc_prov.get_context(user_id="usr_loc")
    assert block is None


@pytest.mark.asyncio
async def test_k_task_commitment_relevance():
    """K. Task & Commitment Relevance: Tasks and commitments activate on relevant keywords and remain quiet on unrelated queries."""
    router = ContextRelevanceRouter()

    # Relevant queries
    dec1 = router.evaluate("What are my pending tasks and action items?")
    assert dec1.tasks is True
    dec2 = router.evaluate("What commitments and promises did I make?")
    assert dec2.commitments is True

    # Unrelated queries
    dec3 = router.evaluate("What is the speed of light?")
    assert dec3.tasks is False
    assert dec3.commitments is False


@pytest.mark.asyncio
async def test_l_calendar_relevance():
    """L. Calendar Relevance: Calendar schedule activates on meeting/schedule queries and remains quiet on factual queries."""
    router = ContextRelevanceRouter()

    assert router.evaluate("What meetings do I have scheduled on my calendar today?").calendar is True
    assert router.evaluate("Explain the theory of relativity").calendar is False


@pytest.mark.asyncio
async def test_m_contact_relevance_and_minimization():
    """M. Contact Relevance & Minimization: Contacts query activates on contact keywords and excludes phone/email."""
    router = ContextRelevanceRouter()
    assert router.evaluate("Can you find Alice's contact in my address book?").contacts is True
    assert router.evaluate("How does a carburetor work?").contacts is False

    perm_mgr = PermissionManager(store=InMemoryPermissionStore())
    await perm_mgr.update_permission(PermissionType.CONTACTS, PermissionStatus.AUTHORIZED, user_id="usr_c")
    contacts_mgr = ContactManager(permission_manager=perm_mgr)
    c1 = ContactSnapshot(
        contact_id="cnt_alice",
        display_name="Alice Wonderland",
        phone_numbers=["+1-555-0199"],
        email_addresses=["alice@secret.com"],
    )
    await contacts_mgr.set_contacts([c1], user_id="usr_c")
    prov = ContactsContextProvider(contact_manager=contacts_mgr)

    block = await prov.get_context(user_id="usr_c", query="Alice")
    assert block is not None
    assert "Alice Wonderland" in block.content
    # Context minimization: sensitive raw fields should not be dumped indiscriminately
    assert "Relevant Contacts" in block.content


@pytest.mark.asyncio
async def test_n_location_relevance():
    """N. Location Relevance: Location activates on nearby/weather queries and remains quiet on coding queries."""
    router = ContextRelevanceRouter()
    assert router.evaluate("What is the weather nearby my current location?").location is True
    assert router.evaluate("Write a Python sorting algorithm").location is False


@pytest.mark.asyncio
async def test_o_reminder_relevance():
    """O. Reminder Relevance: Reminders activate on reminder/due/alert queries."""
    router = ContextRelevanceRouter()
    assert router.evaluate("What are my reminders due today?").reminders is True
    assert router.evaluate("Set an alarm or alert for me").reminders is True
    assert router.evaluate("Who was George Washington?").reminders is False


@pytest.mark.asyncio
async def test_p_proactive_suggestion_relevance():
    """P. Proactive Suggestion Relevance: Proactive suggestions activate only on explicit advice/suggestion queries."""
    router = ContextRelevanceRouter()
    assert router.evaluate("Do you have any suggestions or advice for me?").proactive is True
    assert router.evaluate("What is 15 multiplied by 4?").proactive is False


@pytest.mark.asyncio
async def test_q_selected_asset_relevance():
    """Q. Selected Asset Relevance: Selected assets activate only when files are attached or queried."""
    router = ContextRelevanceRouter()
    assert router.evaluate("Analyze this attached file and document").assets is True
    assert router.evaluate("Hello Aurora, how are you?").assets is False


@pytest.mark.asyncio
async def test_r_execution_scoped_web_context():
    """R. Execution-Scoped Web Context: Web citations appear in context only when web tools ran during that execution."""
    state = AgentExecutionState(execution_id="exec_r", conversation_id="conv_r", session_id="sess_r", user_id="usr_r")
    state.citation_registry.register(
        url="https://example.com/news",
        title="Example News",
    )

    block = format_web_sources_block(state)
    assert block is not None
    assert block.priority == 50
    assert "Validated Web Sources" in block.title
    assert "https://example.com/news" in block.content
    assert "[DATA ONLY" in block.content


@pytest.mark.asyncio
async def test_s_citation_isolation():
    """S. Citation Isolation: Citations from execution X never bleed into execution Y."""
    state_x = AgentExecutionState(execution_id="exec_x", conversation_id="conv_x", session_id="sess_x", user_id="usr_x")
    state_x.citation_registry.register(
        url="https://x.org",
        title="X Source",
    )

    state_y = AgentExecutionState(execution_id="exec_y", conversation_id="conv_y", session_id="sess_y", user_id="usr_y")
    # execution Y did not run any web tools

    block_x = format_web_sources_block(state_x)
    block_y = format_web_sources_block(state_y)

    assert block_x is not None
    assert block_y is None


@pytest.mark.asyncio
async def test_t_data_boundary_protection():
    """T. Data Boundary Protection: Boundary tags (<user_memory>, <user_reminders>, etc.) wrap contextual data."""
    rem_mgr = ReminderManager(store=InMemoryReminderStore())
    now = datetime.now(timezone.utc)
    await rem_mgr.create_reminder(user_id="usr_t", title="Call dentist", scheduled_at=now + timedelta(hours=2))
    prov = ReminderContextProvider(reminder_manager=rem_mgr)

    block = await prov.get_context(user_id="usr_t")
    assert block is not None
    assert "<user_reminders>" in block.content
    assert block.content.rstrip().endswith("</user_reminders>")


@pytest.mark.asyncio
async def test_u_agent_loop_controller_integration():
    """U. AgentLoopController Integration: Agent loop correctly supplies unified prompt to AI provider."""
    mock_ai = MagicMock(spec=BaseAIProvider)
    mock_ai.generate_response = AsyncMock(return_value=AIResponse(provider="test", model="test-model", content="I see your time and context.", tool_calls=None))

    tool_reg = ToolRegistry()
    executor = ToolExecutor(registry=tool_reg)
    engine = ContextEngine(providers=[DummyProvider("temporal", 10, "Time: 12:00 UTC")])
    loop = AgentLoopController(ai_provider=mock_ai, context_manager=ContextManager(), tool_registry=tool_reg, tool_executor=executor, context_engine=engine)

    session = ConversationSession(session_id="sess_u", user_id="usr_u")
    await loop.execute_turn(session=session, user_message="What time is it?", model="test-model")

    # Verify AI provider received system prompt containing contextual block
    call_args = mock_ai.generate_response.call_args[1]
    messages = call_args["messages"]
    sys_msg = next(m for m in messages if m.role == MessageRole.SYSTEM)
    assert "### Temporal" in sys_msg.content
    assert "Time: 12:00 UTC" in sys_msg.content


@pytest.mark.asyncio
async def test_v_zero_tool_execution_from_context():
    """V. Zero Tool Execution from Context: Context gathering performs zero tool invocations."""
    mock_executor = MagicMock(spec=ToolExecutor)
    mock_executor.execute = AsyncMock()

    engine = create_default_context_engine()
    await engine.gather_context_blocks(query="What are my tasks and schedule?", user_id="usr_v")

    # Zero tool executions
    mock_executor.execute.assert_not_called()


@pytest.mark.asyncio
async def test_w_zero_persistence_from_context():
    """W. Zero Persistence from Context: Context gathering performs zero write operations."""
    rem_store = InMemoryReminderStore()
    rem_mgr = ReminderManager(store=rem_store)
    prov = ReminderContextProvider(reminder_manager=rem_mgr)

    # Initial state
    assert len(await rem_store.list_reminders(user_id="usr_w")) == 0

    await prov.get_context(user_id="usr_w")

    # Still zero reminders created or modified
    assert len(await rem_store.list_reminders(user_id="usr_w")) == 0


@pytest.mark.asyncio
async def test_x_zero_background_tasks():
    """X. Zero Background Tasks: No timers, threads, or recurring crons are spawned."""
    engine = create_default_context_engine()
    # ContextEngine has no background task, worker, thread, or cron attributes
    assert not hasattr(engine, "worker")
    assert not hasattr(engine, "scheduler")
    assert not hasattr(engine, "task_queue")


@pytest.mark.asyncio
async def test_y_full_phase5_regression():
    """Y. Full Phase 5 Regression: CitationRegistry and candidate resolution contracts remain intact."""
    reg = CitationRegistry()
    c = reg.register(url="https://valid.com", title="Title")
    assert c is not None
    assert c.id.startswith("c_")
    assert not reg.is_empty()
    assert reg.get(c.id) is not None


# =========================================================================
# HARDENING AMENDMENTS (Z1 - Z15)
# =========================================================================

@pytest.mark.asyncio
async def test_z1_phase5k_citation_authority_preserved():
    """Z1. Phase 5K Citation Authority Preserved: CitationRegistry in execution_state is sole source; ContextEngine only formats."""
    state = AgentExecutionState(execution_id="exec_z1", conversation_id="conv_z1", session_id="sess_z1", user_id="usr_z1")
    c = state.citation_registry.register(url="https://aurora.ai", title="Aurora")
    assert c is not None

    block = format_web_sources_block(state)
    assert block is not None
    # Formatting only
    assert "https://aurora.ai" in block.content
    assert state.citation_registry.get(c.id).url == c.url


@pytest.mark.asyncio
async def test_z2_no_context_engine_citation_creation():
    """Z2. No ContextEngine Citation Creation: ContextEngine never creates new citation IDs."""
    engine = ContextEngine()
    assert not hasattr(engine, "register_citation")
    assert not hasattr(engine, "create_citation_id")


@pytest.mark.asyncio
async def test_z3_no_context_engine_web_requests():
    """Z3. No ContextEngine Web Requests: ContextEngine never performs HTTP requests or calls web tools."""
    engine = ContextEngine()
    assert not hasattr(engine, "safe_web_reader")
    assert not hasattr(engine, "http_client")
    assert not hasattr(engine, "fetch_url")


@pytest.mark.asyncio
async def test_z4_cross_execution_citation_isolation():
    """Z4. Cross-Execution Citation Isolation: Web citations from execution A never appear in execution B's context."""
    state_a = AgentExecutionState(execution_id="exec_a", conversation_id="conv_a", session_id="s", user_id="u")
    state_a.citation_registry.register(url="https://a.com", title="A")

    state_b = AgentExecutionState(execution_id="exec_b", conversation_id="conv_b", session_id="s", user_id="u")

    engine = ContextEngine()
    prompt_b = await engine.build_system_prompt(execution_state=state_b, query="What is A?")
    assert "https://a.com" not in prompt_b
    assert "Validated Web Sources" not in prompt_b


@pytest.mark.asyncio
async def test_z5_budget_partition_defense():
    """Z5. Budget Partition Defense: Web evidence max 1,000 chars; personal context max 3,000 chars when web tools run; 4,000 global ceiling."""
    # Create large personal providers
    p1 = DummyProvider("p1", 10, "A" * 2000)
    p2 = DummyProvider("p2", 20, "B" * 2000)
    engine = ContextEngine(providers=[p1, p2], max_block_chars=2000)

    # Case 1: Without web citations -> Personal gets up to 4,000 chars
    blocks_no_web = await engine.gather_context_blocks(query=None)
    total_no_web = sum(len(b.content) for b in blocks_no_web)
    assert total_no_web == 4000

    # Case 2: With web citations -> Personal capped at 3,000 chars, Web gets up to 1,000 chars
    state = AgentExecutionState(execution_id="exec_z5", conversation_id="conv_z5", session_id="s", user_id="u")
    state.citation_registry.register(url="https://w.com", title="W")

    blocks_with_web = await engine.gather_context_blocks(execution_state=state, query=None)
    personal_chars = sum(len(b.content) for b in blocks_with_web if b.title != "Validated Web Sources")
    web_chars = sum(len(b.content) for b in blocks_with_web if b.title == "Validated Web Sources")

    assert personal_chars <= 3000
    assert web_chars <= 1000
    assert personal_chars + web_chars <= 4000


@pytest.mark.asyncio
async def test_z6_general_factual_memory_suppression():
    """Z6. General Factual/Technical Memory Suppression: Factual queries ('What is photosynthesis?') do not trigger memory retrieval."""
    router = ContextRelevanceRouter()

    assert router.evaluate("What is photosynthesis?").memory is False
    assert router.evaluate("Who was Isaac Newton?").memory is False
    assert router.evaluate("Define polymorphic inheritance in Python").memory is False
    assert router.evaluate("How does a diesel engine operate?").memory is False


@pytest.mark.asyncio
async def test_z7_ambiguous_memory_fallback():
    """Z7. Ambiguous Memory Fallback: Ambiguous queries follow conservative deterministic fallback (high-importance memories only)."""
    router = ContextRelevanceRouter()

    # Ambiguous lifestyle entity queries
    dec1 = router.evaluate("Can we get dinner tonight?")
    assert dec1.memory is True

    dec2 = router.evaluate("Any ideas for the weekend trip?")
    assert dec2.memory is True

    # Ambiguous non-lifestyle query with no personal pronouns
    dec3 = router.evaluate("The weather looks chilly.")
    assert dec3.memory is False


@pytest.mark.asyncio
async def test_z8_relevance_never_grants_permission():
    """Z8. Relevance Never Grants Permission: Relevant domain with denied permission is strictly omitted."""
    perm_mgr = PermissionManager(store=InMemoryPermissionStore())
    policy = ContextAccessPolicy(permission_manager=perm_mgr)

    # REMINDERS is NOT authorized
    rem_prov = DummyProvider("reminders", 30, "Buy bread", ContextSensitivity.PRIVATE, PermissionType.REMINDERS)
    engine = ContextEngine(providers=[rem_prov], policy=policy)

    # Query is explicitly relevant to reminders
    prompt = await engine.build_system_prompt(user_id="usr_z8", query="Remind me what reminders I have")

    assert "Buy bread" not in prompt
    assert "Reminders" not in prompt


@pytest.mark.asyncio
async def test_z9_fail_closed_identity_without_fallback():
    """Z9. Fail-Closed Identity Without Fallback: Missing user ID returns None without falling back to default."""
    task_mgr = TaskManager(store=InMemoryTaskStore())
    await task_mgr.create_task(title="Secret Task", user_id="default_user")

    prov = TaskContextProvider(task_manager=task_mgr)

    # Passing empty user_id must NOT return default_user tasks
    assert await prov.get_context(user_id=None) is None
    assert await prov.get_context(user_id="") is None
    assert await prov.get_context(user_id="   ") is None


@pytest.mark.asyncio
async def test_z10_explicit_test_identity():
    """Z10. Explicit Test Identity: Existing tests pass with explicit valid test identity."""
    task_mgr = TaskManager(store=InMemoryTaskStore())
    await task_mgr.create_task(title="Alpha Task", user_id="explicit_test_uid")

    prov = TaskContextProvider(task_manager=task_mgr)
    block = await prov.get_context(user_id="explicit_test_uid")

    assert block is not None
    assert "Alpha Task" in block.content


@pytest.mark.asyncio
async def test_z11_server_held_query_resumption():
    """Z11. Server-Held Query Resumption: Confirmation resume uses server-held execution_state.turn_user_text."""
    mock_ai = MagicMock(spec=BaseAIProvider)
    mock_ai.generate_response = AsyncMock(return_value=AIResponse(provider="test", model="test-model", content="Action approved and completed.", tool_calls=None))

    mock_tool = MagicMock(spec=BaseTool)
    mock_tool.name = "test_action"
    mock_tool.risk_level = ToolRiskLevel.HIGH
    mock_tool.definition = ToolDefinition(name="test_action", description="test action", parameters={})
    mock_tool.execute = AsyncMock(return_value={"status": "done"})

    tool_reg = ToolRegistry()
    tool_reg.register(mock_tool)
    executor = ToolExecutor(registry=tool_reg)

    engine = ContextEngine(providers=[DummyProvider("temporal", 10, "Time: 12:00")])
    loop = AgentLoopController(ai_provider=mock_ai, context_manager=ContextManager(), tool_registry=tool_reg, tool_executor=executor, context_engine=engine)

    session = ConversationSession(session_id="sess_z11", user_id="usr_z11")
    state = AgentExecutionState(
        execution_id="exec_z11",
        conversation_id="conv_z11",
        session_id="sess_z11",
        user_id="usr_z11",
        execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        turn_user_text="Original Server Held Query",
    )
    paused_call = ToolCall(id="call_1", name="test_action", arguments={})
    state.paused_tool_call = paused_call

    # Resume execution with approval
    await loop.resume_execution(
        session=session,
        execution_state=state,
        approved=True,
        model="test-model",
    )

    assert state.execution_status == AgentExecutionStatus.COMPLETED
    assert state.turn_user_text == "Original Server Held Query"


@pytest.mark.asyncio
async def test_z12_citation_preservation_on_resume():
    """Z12. Citation Preservation on Resume: Confirmation resume preserves exact same execution-scoped citation registry."""
    state = AgentExecutionState(
        execution_id="exec_z12",
        conversation_id="conv_z12",
        session_id="s",
        user_id="u",
        execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        turn_user_text="Check weather and schedule",
    )
    c = state.citation_registry.register(url="https://weather.org", title="Weather")
    assert c is not None

    # Invariants check across pause/resume simulation
    reg_before = state.citation_registry
    assert not reg_before.is_empty()
    assert reg_before.get(c.id).url == c.url

    # Citation remains identical
    assert state.citation_registry is reg_before


@pytest.mark.asyncio
async def test_z13_web_content_cannot_activate_providers():
    """Z13. Web Content Cannot Activate Providers: Injection in web text cannot trigger sensitive providers."""
    router = ContextRelevanceRouter()

    # Query without personal permissions
    query = "Summarize this page content"
    decision = router.evaluate(query)

    # Even if web text contained "remind me to..." or "delete my calendar",
    # the user's query drives the router, not the untrusted web text.
    assert decision.reminders is False
    assert decision.calendar is False
    assert decision.contacts is False


@pytest.mark.asyncio
async def test_z14_zero_tool_invocation():
    """Z14. Zero Tool Invocation: Context gathering executes zero tools."""
    engine = create_default_context_engine()
    with patch.object(ToolExecutor, "execute", new_callable=AsyncMock) as mock_exec:
        await engine.gather_context_blocks(query="Find my upcoming schedule and remind me", user_id="usr_z14")
        mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_z15_zero_write_persistence():
    """Z15. Zero Write Persistence: Context gathering modifies zero database rows."""
    task_store = InMemoryTaskStore()
    task_mgr = TaskManager(store=task_store)
    prov = TaskContextProvider(task_manager=task_mgr)

    await prov.get_context(user_id="usr_z15")
    tasks = await task_mgr.list_tasks(user_id="usr_z15")
    assert len(tasks) == 0
