import pytest
from datetime import datetime, timezone, timedelta

from app.services.context.providers.temporal import TemporalContextProvider
from app.services.context.providers.memory import MemoryContextProvider
from app.services.context.providers.history import ConversationHistoryContextProvider
from app.services.memory.store import InMemoryMemoryStore
from app.services.memory.manager import MemoryManager
from app.services.memory.schemas import MemoryItem, MemoryType, MemoryStatus
from app.services.conversation.session import ConversationSession
from app.services.ai.base import ChatMessage, MessageRole


# MARK: - TemporalContextProvider Tests

@pytest.mark.asyncio
async def test_temporal_provider_deterministic_utc():
    fixed_time = datetime(2026, 9, 21, 14, 30, 0, tzinfo=timezone.utc)
    provider = TemporalContextProvider(
        default_timezone="UTC",
        now_fn=lambda: fixed_time
    )

    block = await provider.get_context()
    assert block is not None
    assert block.title == "Temporal Reference"
    assert "Current Date: Monday, September 21, 2026" in block.content
    assert "Weekday: Monday" in block.content
    assert "Current UTC Time: 2026-09-21 14:30:00 UTC" in block.content
    assert "Reference Timezone: UTC" in block.content


@pytest.mark.asyncio
async def test_temporal_provider_timezone_conversion():
    # 14:30 UTC is 10:30 EDT (America/New_York, UTC-4 in September)
    fixed_time = datetime(2026, 9, 21, 14, 30, 0, tzinfo=timezone.utc)
    provider = TemporalContextProvider(
        default_timezone="America/New_York",
        now_fn=lambda: fixed_time
    )

    block = await provider.get_context()
    assert block is not None
    assert "User Local Time: 2026-09-21 10:30:00 (America/New_York)" in block.content
    assert "Current UTC Time: 2026-09-21 14:30:00 UTC" in block.content


@pytest.mark.asyncio
async def test_temporal_provider_session_override():
    fixed_time = datetime(2026, 9, 21, 14, 30, 0, tzinfo=timezone.utc)
    provider = TemporalContextProvider(
        default_timezone="UTC",
        now_fn=lambda: fixed_time
    )

    # Session specifies user timezone in metadata
    session = ConversationSession(conversation_id="sess-1")
    session.metadata["timezone"] = "Europe/London"

    block = await provider.get_context(session=session)
    assert block is not None
    # London is in BST (UTC+1) in September -> 15:30
    assert "User Local Time: 2026-09-21 15:30:00 (Europe/London)" in block.content


@pytest.mark.asyncio
async def test_temporal_provider_invalid_timezone_fallback():
    fixed_time = datetime(2026, 9, 21, 14, 30, 0, tzinfo=timezone.utc)
    provider = TemporalContextProvider(
        default_timezone="Invalid/Nonexistent_Timezone",
        now_fn=lambda: fixed_time
    )

    # Must fall back gracefully to UTC without raising an exception
    block = await provider.get_context()
    assert block is not None
    assert "Current UTC Time: 2026-09-21 14:30:00 UTC" in block.content
    assert "Reference Timezone: UTC" in block.content


# MARK: - MemoryContextProvider Tests

@pytest.fixture
def memory_manager():
    store = InMemoryMemoryStore()
    return MemoryManager(store=store)


@pytest.mark.asyncio
async def test_memory_provider_relevant_retrieval(memory_manager):
    session = ConversationSession(conversation_id="sess-mem", metadata={"user_id": "usr_local_default"})
    await memory_manager.create_memory_for_user(
        user_id="usr_local_default",
        content="I prefer dark mode in all editors.",
        type=MemoryType.PREFERENCE,
        importance=4,
        tags=["theme", "ui"]
    )
    await memory_manager.create_memory_for_user(
        user_id="usr_local_default",
        content="Flight booking confirmation is ABC123XYZ.",
        type=MemoryType.FACT,
        importance=3,
        tags=["travel"]
    )

    provider = MemoryContextProvider(memory_manager=memory_manager)

    block = await provider.get_context(session=session, query="Which editor theme do I like?")
    assert block is not None
    assert block.title == "Relevant User Memory"
    assert "<user_memory>" in block.content
    assert "[Preference] I prefer dark mode in all editors." in block.content
    assert "(Tags: theme, ui)" in block.content
    # Non-matching flight confirmation must NOT be included
    assert "ABC123XYZ" not in block.content


@pytest.mark.asyncio
async def test_memory_provider_empty_when_no_matches(memory_manager):
    session = ConversationSession(conversation_id="sess-mem-empty", metadata={"user_id": "usr_local_default"})
    await memory_manager.create_memory_for_user(
        user_id="usr_local_default",
        content="Meeting with dentist on Thursday.",
        type=MemoryType.TASK
    )
    provider = MemoryContextProvider(memory_manager=memory_manager)

    block = await provider.get_context(session=session, query="quantum mechanics astrophysics")
    assert block is None


@pytest.mark.asyncio
async def test_memory_provider_excludes_expired_and_archived(memory_manager):
    session = ConversationSession(conversation_id="sess-mem-exp", metadata={"user_id": "usr_local_default"})
    now = datetime.now(timezone.utc)

    # Valid memory
    await memory_manager.create_memory_for_user(
        user_id="usr_local_default",
        content="Current project is Aurora.",
        type=MemoryType.FACT,
        importance=4
    )

    # Expired memory
    await memory_manager.create_memory_for_user(
        user_id="usr_local_default",
        content="Old temporary passcode is 9988.",
        type=MemoryType.FACT,
        expires_at=now - timedelta(days=1)
    )

    # Soft-deleted / archived memory
    item, _ = await memory_manager.create_memory_for_user(
        user_id="usr_local_default",
        content="Archived note about previous project.",
        type=MemoryType.EPISODIC
    )
    await memory_manager.delete_memory_for_user(user_id="usr_local_default", memory_id=item.id, soft=True)

    provider = MemoryContextProvider(memory_manager=memory_manager)
    block = await provider.get_context(session=session, query="project passcode")
    assert block is not None
    assert "Current project is Aurora" in block.content
    assert "9988" not in block.content
    assert "Archived note" not in block.content


@pytest.mark.asyncio
async def test_memory_provider_respects_max_count(memory_manager):
    session = ConversationSession(conversation_id="sess-mem-max", metadata={"user_id": "usr_local_default"})
    for i in range(10):
        await memory_manager.create_memory_for_user(
            user_id="usr_local_default",
            content=f"Important research reference paper {i}.",
            type=MemoryType.FACT,
            importance=3
        )

    provider = MemoryContextProvider(memory_manager=memory_manager, max_memories=3)
    block = await provider.get_context(session=session, query="research reference paper")
    assert block is not None
    assert block.metadata["retrieved_count"] == 3


# MARK: - ConversationHistoryContextProvider Tests

@pytest.mark.asyncio
async def test_history_provider_disabled_by_default():
    session = ConversationSession(conversation_id="sess-1")
    session.add_message(role=MessageRole.USER, content="Hello")
    session.add_message(role=MessageRole.ASSISTANT, content="Hi there!")

    provider = ConversationHistoryContextProvider()
    # Default is disabled to preserve canonical message stream
    block = await provider.get_context(session=session)
    assert block is None


@pytest.mark.asyncio
async def test_history_provider_bounded_when_enabled():
    session = ConversationSession(conversation_id="sess-1")
    for i in range(10):
        session.add_message(role=MessageRole.USER, content=f"Question {i}")
        session.add_message(role=MessageRole.ASSISTANT, content=f"Answer {i}")

    # Explicitly enabled with max_turns=4
    provider = ConversationHistoryContextProvider(max_turns=4, enabled=True)
    block = await provider.get_context(session=session)
    assert block is not None
    assert block.metadata["turn_count"] == 4
    assert "- Assistant: Answer 9" in block.content


@pytest.mark.asyncio
async def test_history_provider_empty_session():
    session = ConversationSession(conversation_id="sess-empty")
    provider = ConversationHistoryContextProvider(enabled=True)
    block = await provider.get_context(session=session)
    assert block is None
