from abc import ABC, abstractmethod
from typing import List, Optional
from app.services.reminders.models import Reminder, ReminderStatus


class BaseReminderStore(ABC):
    """
    Abstract interface for persisting and querying Reminder models.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the store connection, tables, or schema."""
        pass

    @abstractmethod
    async def put(self, reminder: Reminder) -> Reminder:
        """Create or update a reminder record."""
        pass

    @abstractmethod
    async def save_mutation(self, reminder: Reminder) -> tuple[Reminder, bool]:
        """
        Persist a reminder mutation with atomic concurrency and strict monotonicity.
        Returns (persisted_reminder, mutated_boolean).
        If the mutation is an effective no-op, mutated_boolean is False and updated_at is unchanged.
        """
        pass

    @abstractmethod
    async def get(self, reminder_id: str, user_id: str) -> Optional[Reminder]:
        """Fetch a single reminder by id, enforcing user scoping."""
        pass

    @abstractmethod
    async def delete(self, reminder_id: str, user_id: str, soft: bool = True) -> bool:
        """Delete a reminder (soft by default). Return True if found and deleted."""
        pass

    @abstractmethod
    async def list_reminders(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Reminder]:
        """Query reminders strictly scoped to the active user."""
        pass

    @abstractmethod
    async def count(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        """Count matching reminders for pagination."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources / connections."""
        pass
