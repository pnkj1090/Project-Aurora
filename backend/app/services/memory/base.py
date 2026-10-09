from abc import ABC, abstractmethod
from typing import List, Optional
from app.services.memory.schemas import MemoryItem, MemoryType, MemoryStatus, MemorySource


class BaseMemoryStore(ABC):
    """
    Abstract interface for persistent memory storage.
    Provider-agnostic contract supporting SQLite, In-Memory, and future vector stores.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize connection, tables, indexes, or collections."""
        pass

    @abstractmethod
    async def put(self, memory: MemoryItem) -> MemoryItem:
        """
        Insert or update a memory item.
        Updates the updated_at timestamp and returns the saved MemoryItem.
        """
        pass

    @abstractmethod
    async def get(self, memory_id: str) -> Optional[MemoryItem]:
        """
        Retrieve a single memory item by its ID.
        Returns None if not found.
        """
        pass

    @abstractmethod
    async def delete(self, memory_id: str, soft: bool = True) -> bool:
        """
        Delete a memory item.
        If soft=True (default), marks is_active=False and status=ARCHIVED.
        If soft=False, permanently deletes the record.
        Returns True if the item existed and was modified/deleted, False otherwise.
        """
        pass

    @abstractmethod
    async def list_memories(
        self,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryItem]:
        """
        List memories matching optional filters, ordered by created_at descending.
        """
        pass

    @abstractmethod
    async def search_lexical(
        self,
        query: str,
        types: Optional[List[MemoryType]] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        limit: int = 10,
    ) -> List[MemoryItem]:
        """
        Case-insensitive keyword search matching memory content, tags, and type.
        Results are ordered by relevance and importance.
        """
        pass

    @abstractmethod
    async def count(
        self,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = False,
    ) -> int:
        """Return the total number of memories stored matching filter criteria."""
        pass

    @abstractmethod
    def search_candidates_sync(
        self,
        query: Optional[str] = None,
        memory_id: Optional[str] = None,
        limit: int = 5
    ) -> List[MemoryItem]:
        """
        Fast synchronous candidate lookup for tool confirmation checks.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close connection or release resources."""
        pass
