from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

from app.services.tasks.models import Task, TaskStatus, TaskPriority


class BaseTaskStore(ABC):
    """
    Abstract interface for task persistence stores.
    Supports in-memory test mocks and persistent SQLite implementations.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize storage tables or resources."""
        pass

    @abstractmethod
    async def put(self, task: Task) -> Task:
        """Insert or update a task."""
        pass

    @abstractmethod
    async def save_mutation(self, task: Task) -> tuple[Task, bool]:
        """
        Authoritative store-level mutation boundary.
        Returns (persisted_or_updated_task, changed).
        If changed is True, updated_at is strictly advanced.
        If changed is False (strict no-op), updated_at is preserved.
        """
        pass

    @abstractmethod
    async def mutate(self, task_id: str, user_id: str, mutation: Callable[[Task], None]) -> Tuple[Optional[Task], bool]:
        """Apply a read/validate/write mutation to the authoritative row atomically."""
        pass

    @abstractmethod
    async def get(self, task_id: str, user_id: str) -> Optional[Task]:
        """Retrieve a task by ID scoped to the user."""
        pass

    @abstractmethod
    async def delete(self, task_id: str, user_id: str, soft: bool = True) -> bool:
        """Soft delete (is_active=False) or hard remove a task."""
        pass

    @abstractmethod
    async def list_tasks(
        self,
        user_id: str,
        statuses: Optional[List[TaskStatus]] = None,
        priorities: Optional[List[TaskPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Task]:
        """Query tasks with user scoping, filtering, search, and pagination."""
        pass

    @abstractmethod
    async def count(
        self,
        user_id: str,
        statuses: Optional[List[TaskStatus]] = None,
        priorities: Optional[List[TaskPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        """Count tasks matching query and filters."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Clean up store resources."""
        pass
