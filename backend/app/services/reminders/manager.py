import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.reminders.models import Reminder, ReminderStatus
from app.services.reminders.base import BaseReminderStore
from app.services.reminders.store import SQLiteReminderStore
from app.services.tasks import TaskManager, get_task_manager
from app.services.tasks.models import TaskStatus
from app.services.commitments import CommitmentManager, get_commitment_manager
from app.services.commitments.models import CommitmentStatus

logger = logging.getLogger("aurora.services.reminders")


class ReminderManager:
    """
    Domain manager governing explicit reminder scheduling, validation,
    normalization, user ownership isolation, and lifecycle transitions.
    """

    def __init__(
        self,
        store: Optional[BaseReminderStore] = None,
        task_manager: Optional[TaskManager] = None,
        commitment_manager: Optional[CommitmentManager] = None,
    ):
        self._store = store or SQLiteReminderStore()
        self._task_manager = task_manager
        self._commitment_manager = commitment_manager

    @property
    def store(self) -> BaseReminderStore:
        return self._store

    @property
    def task_manager(self) -> TaskManager:
        if self._task_manager is None:
            self._task_manager = get_task_manager()
        return self._task_manager

    @property
    def commitment_manager(self) -> CommitmentManager:
        if self._commitment_manager is None:
            self._commitment_manager = get_commitment_manager()
        return self._commitment_manager

    async def create_reminder(
        self,
        title: str,
        scheduled_at: datetime,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        body: Optional[str] = "",
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        notification_id: Optional[str] = None,
    ) -> Reminder:
        """
        Create a new explicit scheduled reminder for the user.
        Validates ownership of referenced task or commitment.
        Disallows referencing both task and commitment simultaneously.
        """
        clean_title = (title or "").strip()
        if not clean_title:
            raise ValueError("Reminder title cannot be empty.")

        if not scheduled_at:
            raise ValueError("Parameter 'scheduled_at' is required.")

        # Normalize scheduled_at to UTC
        if scheduled_at.tzinfo is None:
            utc_scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        else:
            utc_scheduled_at = scheduled_at.astimezone(timezone.utc)

        # Validate mutual exclusivity of task_id and commitment_id
        if task_id and commitment_id:
            raise ValueError("A reminder cannot reference both a task and a commitment.")

        # Validate user ownership of task_id if provided
        if task_id:
            task = await self.task_manager.get_task(task_id=task_id, user_id=user_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' does not exist or does not belong to user '{user_id}'.")

        # Validate user ownership of commitment_id if provided
        if commitment_id:
            commitment = await self.commitment_manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
            if commitment is None:
                raise ValueError(f"Commitment '{commitment_id}' does not exist or does not belong to user '{user_id}'.")

        reminder = Reminder(
            title=clean_title,
            body=(body or "").strip(),
            scheduled_at=utc_scheduled_at,
            user_id=user_id,
            status=ReminderStatus.SCHEDULED,
            task_id=task_id,
            commitment_id=commitment_id,
            notification_id=notification_id or None,
            is_active=True,
        )
        if not reminder.notification_id:
            reminder.notification_id = reminder.id

        saved = await self._store.put(reminder)
        logger.info("Created reminder '%s' for user '%s' scheduled at %s", saved.id, user_id, saved.scheduled_at)
        return saved

    async def get_reminder(self, reminder_id: str, user_id: str) -> Optional[Reminder]:
        """Fetch reminder strictly scoped to current user."""
        return await self._store.get(reminder_id=reminder_id, user_id=user_id)

    async def list_reminders(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Reminder]:
        """List reminders strictly scoped to active user."""
        return await self._store.list_reminders(
            user_id=user_id,
            statuses=statuses,
            task_id=task_id,
            commitment_id=commitment_id,
            query=query,
            include_inactive=include_inactive,
            limit=limit,
            offset=offset,
        )

    async def count_reminders(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
    ) -> int:
        return await self._store.count(
            user_id=user_id,
            statuses=statuses,
            task_id=task_id,
            commitment_id=commitment_id,
            query=query,
            include_inactive=include_inactive,
        )

    async def update_reminder(
        self,
        reminder_id: str,
        user_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        clear_task: bool = False,
        clear_commitment: bool = False,
    ) -> Optional[Reminder]:
        """
        Update user-editable fields (title, body, scheduled_at, task_id, commitment_id).
        Status changes must use dedicated lifecycle methods (cancel, mark_delivered).
        """
        reminder, _ = await self.update_reminder_with_status(
            reminder_id=reminder_id,
            user_id=user_id,
            title=title,
            body=body,
            scheduled_at=scheduled_at,
            task_id=task_id,
            commitment_id=commitment_id,
            clear_task=clear_task,
            clear_commitment=clear_commitment,
        )
        return reminder

    async def update_reminder_with_status(
        self,
        reminder_id: str,
        user_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        scheduled_at: Optional[datetime] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        clear_task: bool = False,
        clear_commitment: bool = False,
    ) -> Tuple[Optional[Reminder], bool]:
        """
        Atomic update returning (reminder, mutated).
        If the mutation is a strict no-op, mutated is False and updated_at is unchanged.
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        existing = await self.get_reminder(reminder_id=reminder_id, user_id=user_id)
        if existing is None or not existing.is_active:
            return None, False

        if existing.status in (ReminderStatus.CANCELLED, ReminderStatus.DELIVERED):
            raise ValueError(f"Cannot update a reminder with status '{existing.status.value}'.")

        # Validate mutual exclusivity of links
        target_task_id = existing.task_id
        target_commitment_id = existing.commitment_id

        if clear_task:
            target_task_id = None
        if clear_commitment:
            target_commitment_id = None

        if task_id is not None:
            target_task_id = task_id
            if commitment_id is None and not clear_commitment:
                target_commitment_id = None  # Switch clears other link

        if commitment_id is not None:
            target_commitment_id = commitment_id
            if task_id is None and not clear_task:
                target_task_id = None  # Switch clears other link

        if target_task_id and target_commitment_id:
            raise ValueError("A reminder cannot reference both a task and a commitment.")

        if task_id:
            task = await self.task_manager.get_task(task_id=task_id, user_id=user_id)
            if task is None or not task.is_active:
                raise ValueError(f"Task '{task_id}' does not exist, is inactive, or does not belong to user.")
            if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                raise ValueError(f"Cannot link reminder to an inactive or completed task.")

        if commitment_id:
            commitment = await self.commitment_manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
            if commitment is None or not commitment.is_active:
                raise ValueError(f"Commitment '{commitment_id}' does not exist, is inactive, or does not belong to user.")
            if commitment.status in (CommitmentStatus.COMPLETED, CommitmentStatus.CANCELLED):
                raise ValueError(f"Cannot link reminder to an inactive, fulfilled, or cancelled commitment.")

        existing.task_id = target_task_id
        existing.commitment_id = target_commitment_id

        if title is not None:
            clean_title = title.strip()
            if not clean_title:
                raise ValueError("Reminder title cannot be empty.")
            existing.title = clean_title

        if body is not None:
            existing.body = body.strip()

        if scheduled_at is not None:
            if scheduled_at.tzinfo is None:
                existing.scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            else:
                existing.scheduled_at = scheduled_at.astimezone(timezone.utc)

        saved, mutated = await self._store.save_mutation(existing)
        return saved, mutated

    async def snooze_reminder(
        self,
        reminder_id: str,
        user_id: str,
        duration_minutes: int = 15,
    ) -> Tuple[Reminder, bool]:
        """
        Deterministically snooze a reminder forward:
        - Future reminder (scheduled_at > now): new_scheduled_at = scheduled_at + duration_minutes
        - Overdue reminder (scheduled_at <= now): new_scheduled_at = now + duration_minutes
        - Bounds: 5 to 1440 minutes (24 hours).
        - Delivered reminders reset to SCHEDULED.
        - Cancelled reminders reject snooze.
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        if duration_minutes < 5:
            raise ValueError("Snooze duration must be at least 5 minutes.")
        if duration_minutes > 1440:
            raise ValueError("Snooze duration cannot exceed 24 hours (1440 minutes). Use update_reminder to reschedule beyond 24 hours.")

        existing = await self.get_reminder(reminder_id=reminder_id, user_id=user_id)
        if existing is None or not existing.is_active:
            raise ValueError(f"Reminder '{reminder_id}' not found.")

        if existing.status == ReminderStatus.CANCELLED:
            raise ValueError("Cannot snooze a cancelled reminder.")

        now_utc = datetime.now(timezone.utc)
        if existing.scheduled_at > now_utc:
            new_scheduled_at = existing.scheduled_at + timedelta(minutes=duration_minutes)
        else:
            new_scheduled_at = now_utc + timedelta(minutes=duration_minutes)

        existing.scheduled_at = new_scheduled_at
        if existing.status == ReminderStatus.DELIVERED:
            existing.status = ReminderStatus.SCHEDULED

        saved, mutated = await self._store.save_mutation(existing)
        logger.info("Snoozed reminder '%s' for user '%s' to %s (mutated=%s)", reminder_id, user_id, saved.scheduled_at, mutated)
        return saved, mutated

    async def link_reminder(
        self,
        reminder_id: str,
        user_id: str,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
    ) -> Tuple[Reminder, bool]:
        """
        Link a reminder to a task XOR commitment.
        Enforces ownership, active status, and mutual exclusivity.
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        if task_id and commitment_id:
            raise ValueError("A reminder cannot reference both a task and a commitment.")

        if not task_id and not commitment_id:
            raise ValueError("Either task_id or commitment_id must be provided to link.")

        existing = await self.get_reminder(reminder_id=reminder_id, user_id=user_id)
        if existing is None or not existing.is_active:
            raise ValueError(f"Reminder '{reminder_id}' not found.")

        if task_id:
            task = await self.task_manager.get_task(task_id=task_id, user_id=user_id)
            if task is None or not task.is_active:
                raise ValueError(f"Task '{task_id}' does not exist, is inactive, or does not belong to user.")
            if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                raise ValueError(f"Cannot link reminder to an inactive or completed task.")
            existing.task_id = task_id
            existing.commitment_id = None

        if commitment_id:
            commitment = await self.commitment_manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
            if commitment is None or not commitment.is_active:
                raise ValueError(f"Commitment '{commitment_id}' does not exist, is inactive, or does not belong to user.")
            if commitment.status in (CommitmentStatus.COMPLETED, CommitmentStatus.CANCELLED):
                raise ValueError(f"Cannot link reminder to an inactive, fulfilled, or cancelled commitment.")
            existing.commitment_id = commitment_id
            existing.task_id = None

        saved, mutated = await self._store.save_mutation(existing)
        logger.info("Linked reminder '%s' to %s (mutated=%s)", reminder_id, f"task {task_id}" if task_id else f"commitment {commitment_id}", mutated)
        return saved, mutated

    async def unlink_reminder(
        self,
        reminder_id: str,
        user_id: str,
        clear_task: bool = True,
        clear_commitment: bool = True,
    ) -> Tuple[Reminder, bool]:
        """
        Unlink reminder from any attached task or commitment.
        If already unlinked, returns a strict no-op with mutated=False.
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        existing = await self.get_reminder(reminder_id=reminder_id, user_id=user_id)
        if existing is None or not existing.is_active:
            raise ValueError(f"Reminder '{reminder_id}' not found.")

        if clear_task:
            existing.task_id = None
        if clear_commitment:
            existing.commitment_id = None
        saved, mutated = await self._store.save_mutation(existing)
        return saved, mutated

    async def cancel_reminder(self, reminder_id: str, user_id: str) -> Optional[Reminder]:
        """Dedicated lifecycle method to cancel a reminder."""
        reminder, _ = await self.cancel_reminder_with_status(reminder_id=reminder_id, user_id=user_id)
        return reminder

    async def cancel_reminder_with_status(self, reminder_id: str, user_id: str) -> Tuple[Optional[Reminder], bool]:
        """Cancel reminder returning (reminder, mutated). If already cancelled, returns (existing, False)."""
        existing = await self.get_reminder(reminder_id=reminder_id, user_id=user_id)
        if existing is None or not existing.is_active:
            return None, False

        if existing.status == ReminderStatus.CANCELLED:
            return existing, False

        existing.status = ReminderStatus.CANCELLED
        saved, mutated = await self._store.save_mutation(existing)
        logger.info("Cancelled reminder '%s' for user '%s' (mutated=%s)", reminder_id, user_id, mutated)
        return saved, mutated

    async def mark_delivered(self, reminder_id: str, user_id: str) -> Optional[Reminder]:
        """Mark a reminder as delivered (reported by client/system)."""
        existing = await self.get_reminder(reminder_id=reminder_id, user_id=user_id)
        if existing is None or not existing.is_active:
            return None

        existing.status = ReminderStatus.DELIVERED
        existing.updated_at = datetime.now(timezone.utc)
        saved = await self._store.put(existing)
        logger.info("Marked reminder '%s' as delivered for user '%s'", reminder_id, user_id)
        return saved

    async def delete_reminder(self, reminder_id: str, user_id: str, soft: bool = True) -> bool:
        """Safe soft deletion of a reminder."""
        return await self._store.delete(reminder_id=reminder_id, user_id=user_id, soft=soft)


# Global singleton instance
_default_reminder_manager: Optional[ReminderManager] = None


def get_reminder_manager(
    store: Optional[BaseReminderStore] = None,
    task_manager: Optional[TaskManager] = None,
    commitment_manager: Optional[CommitmentManager] = None,
) -> ReminderManager:
    """Retrieve or construct the ReminderManager instance."""
    global _default_reminder_manager
    if store is not None or task_manager is not None or commitment_manager is not None:
        return ReminderManager(
            store=store,
            task_manager=task_manager,
            commitment_manager=commitment_manager,
        )
    if _default_reminder_manager is None:
        _default_reminder_manager = ReminderManager()
    return _default_reminder_manager


def get_reminder_manager_dependency() -> ReminderManager:
    """FastAPI dependency provider."""
    return get_reminder_manager()
