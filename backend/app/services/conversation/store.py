import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Optional, List

from app.services.conversation.session import ConversationSession


class BaseConversationStore(ABC):
    """
    Abstract storage interface for conversation sessions.
    Allows replacing in-memory storage with PostgreSQL, SQLite, or Redis in future phases.
    """

    @abstractmethod
    async def get(self, conversation_id: str) -> Optional[ConversationSession]:
        pass

    @abstractmethod
    async def save(self, session: ConversationSession) -> None:
        pass

    @abstractmethod
    async def create(self, conversation_id: Optional[str] = None) -> ConversationSession:
        pass

    @abstractmethod
    async def list_all(self) -> List[ConversationSession]:
        pass

    @abstractmethod
    async def delete(self, conversation_id: str) -> bool:
        pass


class InMemoryConversationStore(BaseConversationStore):
    """
    Thread-safe in-memory conversation repository for Phase 2 development and testing.
    """

    def __init__(self):
        self._sessions: Dict[str, ConversationSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, conversation_id: str) -> Optional[ConversationSession]:
        async with self._lock:
            return self._sessions.get(conversation_id)

    async def save(self, session: ConversationSession) -> None:
        async with self._lock:
            self._sessions[session.conversation_id] = session

    async def create(self, conversation_id: Optional[str] = None) -> ConversationSession:
        async with self._lock:
            if conversation_id and conversation_id in self._sessions:
                return self._sessions[conversation_id]
            session = ConversationSession(conversation_id=conversation_id) if conversation_id else ConversationSession()
            self._sessions[session.conversation_id] = session
            return session

    async def list_all(self) -> List[ConversationSession]:
        async with self._lock:
            return list(self._sessions.values())

    async def delete(self, conversation_id: str) -> bool:
        async with self._lock:
            if conversation_id in self._sessions:
                del self._sessions[conversation_id]
                return True
            return False


# Singleton in-memory store instance for application lifecycle
default_conversation_store = InMemoryConversationStore()
