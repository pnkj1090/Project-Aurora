from typing import Optional
from app.core.config import settings
from app.services.memory.schemas import MemoryItem, MemoryType, MemorySource, MemoryStatus
from app.services.memory.base import BaseMemoryStore
from app.services.memory.store import InMemoryMemoryStore, SQLiteMemoryStore
from app.services.memory.retriever import BaseMemoryRetriever, LexicalMemoryRetriever
from app.services.memory.manager import MemoryManager

_default_store: Optional[BaseMemoryStore] = None
_default_manager: Optional[MemoryManager] = None


def get_memory_store(db_path: Optional[str] = None) -> BaseMemoryStore:
    """
    Factory function providing the default persistent SQLite memory store.
    """
    global _default_store
    if _default_store is None:
        path = db_path or settings.MEMORY_DB_PATH
        _default_store = SQLiteMemoryStore(db_path=path)
    return _default_store


def get_memory_manager(store: Optional[BaseMemoryStore] = None) -> MemoryManager:
    """
    Factory function providing the default MemoryManager.
    """
    global _default_manager
    if _default_manager is None or store is not None:
        st = store or get_memory_store()
        retriever = LexicalMemoryRetriever(store=st)
        mgr = MemoryManager(store=st, retriever=retriever)
        if store is None:
            _default_manager = mgr
        return mgr
    return _default_manager


def get_memory_manager_dependency() -> MemoryManager:
    """Dependency provider for FastAPI route handlers with zero arguments."""
    return get_memory_manager()


__all__ = [
    "MemoryItem",
    "MemoryType",
    "MemorySource",
    "MemoryStatus",
    "BaseMemoryStore",
    "InMemoryMemoryStore",
    "SQLiteMemoryStore",
    "BaseMemoryRetriever",
    "LexicalMemoryRetriever",
    "MemoryManager",
    "get_memory_store",
    "get_memory_manager",
    "get_memory_manager_dependency",
]
