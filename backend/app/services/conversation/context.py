from typing import List, Optional
from app.services.ai.base import ChatMessage, MessageRole
from app.services.conversation.session import ConversationSession
from app.services.conversation.prompts import get_default_system_prompt


class ContextManager:
    """
    Context Window Manager responsible for preparing the message history sent to the model.
    Prevents unbounded context growth and provides clean extension points for:
    - Token counting and budget allocation
    - Rolling window / message truncation
    - Long-term memory retrieval
    - Conversation summarization
    """

    def __init__(self, max_history_messages: int = 20):
        self.max_history_messages = max_history_messages

    def prepare_messages(
        self,
        session: ConversationSession,
        custom_system_prompt: Optional[str] = None
    ) -> List[ChatMessage]:
        """
        Assemble the final message list for the AI provider:
        1. System instruction (Aurora identity + context)
        2. Bounded conversation history (sliding window)
        """
        system_text = custom_system_prompt or get_default_system_prompt()
        system_message = ChatMessage(role=MessageRole.SYSTEM, content=system_text)

        # Apply rolling window to prevent context overflow
        # Retain the most recent `max_history_messages` from the session
        history = session.messages[-self.max_history_messages:] if len(session.messages) > self.max_history_messages else session.messages

        return [system_message] + list(history)
