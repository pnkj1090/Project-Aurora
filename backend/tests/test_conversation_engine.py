import pytest
from app.services.ai.base import MessageRole
from app.services.ai.mock_provider import MockProvider
from app.services.conversation.session import ConversationSession
from app.services.conversation.store import InMemoryConversationStore
from app.services.conversation.prompts import SystemPromptBuilder, get_default_system_prompt
from app.services.conversation.context import ContextManager
from app.services.conversation.service import ConversationService


@pytest.mark.asyncio
async def test_session_message_management():
    session = ConversationSession()
    assert session.conversation_id is not None
    assert len(session.messages) == 0

    msg = session.add_message(MessageRole.USER, "What is the capital of France?")
    assert msg.content == "What is the capital of France?"
    assert len(session.messages) == 1
    assert session.messages[0].role == MessageRole.USER


@pytest.mark.asyncio
async def test_in_memory_store():
    store = InMemoryConversationStore()
    session = await store.create("session-123")
    assert session.conversation_id == "session-123"

    session.add_message(MessageRole.USER, "Test 1")
    await store.save(session)

    retrieved = await store.get("session-123")
    assert retrieved is not None
    assert len(retrieved.messages) == 1
    assert retrieved.messages[0].content == "Test 1"

    all_sessions = await store.list_all()
    assert len(all_sessions) == 1

    deleted = await store.delete("session-123")
    assert deleted is True
    assert await store.get("session-123") is None


def test_system_prompt_builder():
    builder = SystemPromptBuilder("Base Aurora Prompt.")
    prompt_base = builder.build()
    assert "Base Aurora Prompt." in prompt_base

    builder.add_context_block("User Preferences", "Prefers concise responses.")
    prompt_with_ctx = builder.build()
    assert "User Preferences" in prompt_with_ctx
    assert "Prefers concise responses." in prompt_with_ctx


def test_context_manager_sliding_window():
    session = ConversationSession()
    for i in range(30):
        session.add_message(MessageRole.USER, f"Message {i}")

    manager = ContextManager(max_history_messages=10)
    prepared = manager.prepare_messages(session)

    # 1 system message + 10 history messages = 11 total
    assert len(prepared) == 11
    assert prepared[0].role == MessageRole.SYSTEM
    assert prepared[1].content == "Message 20"
    assert prepared[-1].content == "Message 29"


@pytest.mark.asyncio
async def test_conversation_service_multi_turn():
    store = InMemoryConversationStore()
    provider = MockProvider()
    service = ConversationService(store=store, ai_provider=provider)

    # Turn 1
    session, assistant_msg1, _, pending1, _ = await service.process_chat(
        user_text="My name is Alex.",
        conversation_id=None
    )
    conv_id = session.conversation_id
    assert conv_id is not None
    assert pending1 is None
    assert len(session.messages) == 2
    assert session.messages[0].content == "My name is Alex."
    assert session.messages[1].role == MessageRole.ASSISTANT

    # Turn 2 using the same conversation_id
    session2, assistant_msg2, _, pending2, _ = await service.process_chat(
        user_text="What is my name?",
        conversation_id=conv_id
    )
    assert session2.conversation_id == conv_id
    assert pending2 is None
    assert len(session2.messages) == 4
    assert session2.messages[2].content == "What is my name?"

    # Verify session persisted in store
    retrieved = await store.get(conv_id)
    assert retrieved is not None
    assert len(retrieved.messages) == 4
