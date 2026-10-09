from typing import Optional
from app.core.config import settings
from app.services.tasks.models import Task, TaskStatus, TaskPriority
from app.services.tasks.base import BaseTaskStore
from app.services.tasks.store import InMemoryTaskStore, SQLiteTaskStore
from app.services.tasks.manager import TaskManager

_default_task_store: Optional[BaseTaskStore] = None
_default_task_manager: Optional[TaskManager] = None


def get_task_store(db_path: Optional[str] = None) -> BaseTaskStore:
    """Factory function providing the default persistent SQLite task store."""
    global _default_task_store
    if _default_task_store is None or db_path is not None:
        path = db_path or settings.DATABASE_PATH
        st = SQLiteTaskStore(db_path=path)
        if db_path is None:
            _default_task_store = st
        return st
    return _default_task_store


def get_task_manager(store: Optional[BaseTaskStore] = None) -> TaskManager:
    """Factory function providing the TaskManager singleton or instance."""
    global _default_task_manager
    if _default_task_manager is None or store is not None:
        st = store or get_task_store()
        mgr = TaskManager(store=st)
        if store is None:
            _default_task_manager = mgr
        return mgr
    return _default_task_manager


def get_task_manager_dependency() -> TaskManager:
    """Dependency provider for FastAPI route handlers with zero arguments."""
    return get_task_manager()


__all__ = [
    "Task",
    "TaskStatus",
    "TaskPriority",
    "BaseTaskStore",
    "InMemoryTaskStore",
    "SQLiteTaskStore",
    "TaskManager",
    "get_task_store",
    "get_task_manager",
    "get_task_manager_dependency",
]
