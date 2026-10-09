from typing import Optional, List, Any
import logging

from app.services.context.base import BaseContextProvider, ContextBlock

logger = logging.getLogger("aurora.context.history")


class ConversationHistoryContextProvider(BaseContextProvider):
    """
    Optional context provider for summarizing or extracting prior dialog context.
    NOTE: In Project Aurora's architecture, the ChatMessage sequence managed by
    ContextManager is the canonical source of recent conversation history.
    This provider remains available for specialized cross-session or summary scenarios,
    but is not active in default system prompt assembly to avoid duplicate message injection.
    """

    def __init__(
        self,
        max_turns: int = 5,
        priority: int = 30,
        enabled: bool = False,
    ):
        self.max_turns = max_turns
        self._priority = priority
        self.enabled = enabled

    @property
    def name(self) -> str:
        return "conversation_history"

    @property
    def priority(self) -> int:
        return self._priority

    async def get_context(
        self,
        session: Optional[Any] = None,
        query: Optional[str] = None,
        **kwargs: Any
    ) -> Optional[ContextBlock]:
        if not self.enabled and not kwargs.get("force_enabled", False):
            return None

        if not session or not hasattr(session, "messages") or not session.messages:
            return None

        # Exclude the very last user message if it matches query (to represent historical turns)
        messages = list(session.messages)
        if messages and messages[-1].role.value == "user" and (not query or messages[-1].content == query):
            history_pool = messages[:-1]
        else:
            history_pool = messages

        if not history_pool:
            return None

        # Take bounded recent history
        bounded = history_pool[-self.max_turns:]
        lines: List[str] = []
        for msg in bounded:
            role_label = msg.role.value.capitalize()
            # Sanitize single-line representation
            content_snippet = " ".join(msg.content.split()) if msg.content else "[empty]"
            lines.append(f"- {role_label}: {content_snippet}")

        if not lines:
            return None

        return ContextBlock(
            title="Prior Conversation Context",
            content="\n".join(lines),
            priority=self.priority,
            metadata={"turn_count": len(lines)}
        )
