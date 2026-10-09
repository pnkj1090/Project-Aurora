import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.tasks.base import BaseTaskStore
from app.services.tasks.models import Task, TaskStatus, TaskPriority
from app.services.tasks.store import SQLiteTaskStore

logger = logging.getLogger(__name__)


class TaskManager:
    """
    Domain service for managing task lifecycles, user-scoped queries,
    and business rule enforcement.
    """

    def __init__(
        self,
        store: Optional[BaseTaskStore] = None,
        commitment_manager: Optional[Any] = None
    ):
        self._store = store or SQLiteTaskStore()
        self._commitment_manager = commitment_manager

    @property
    def store(self) -> BaseTaskStore:
        return self._store

    @property
    def commitment_manager(self):
        if self._commitment_manager is None:
            from app.services.commitments import get_commitment_manager
            self._commitment_manager = get_commitment_manager()
        return self._commitment_manager

    async def initialize(self) -> None:
        """Ensure storage is ready."""
        await self._store.initialize()

    async def create_task(
        self,
        title: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        description: Optional[str] = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        due_at: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source_memory_id: Optional[str] = None,
        source_commitment_id: Optional[str] = None
    ) -> Task:
        """
        Create and persist a new actionable task.
        Metadata and source_memory_id are internal-only domain fields.
        """
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title cannot be empty.")

        if source_commitment_id:
            commitment = await self.commitment_manager.get_commitment(
                commitment_id=source_commitment_id,
                user_id=user_id
            )
            if commitment is None:
                raise ValueError(f"Commitment '{source_commitment_id}' does not exist or does not belong to user '{user_id}'.")

        task = Task(
            user_id=user_id,
            title=clean_title,
            description=(description or "").strip(),
            status=TaskStatus.PENDING,
            priority=priority,
            due_at=due_at,
            tags=tags or [],
            metadata=metadata or {},
            is_active=True,
            source_memory_id=source_memory_id,
            source_commitment_id=source_commitment_id
        )
        return await self._store.put(task)

    async def get_task(self, task_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> Optional[Task]:
        """Retrieve a task scoped strictly to the current user."""
        return await self._store.get(task_id=task_id, user_id=user_id)

    async def list_tasks(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        statuses: Optional[List[TaskStatus]] = None,
        priorities: Optional[List[TaskPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Task]:
        """
        List tasks strictly scoped to the user with optional filters and search.
        Never queries all tasks and filters afterwards.
        """
        return await self._store.list_tasks(
            user_id=user_id,
            statuses=statuses,
            priorities=priorities,
            tags=tags,
            query=query,
            include_inactive=include_inactive,
            limit=limit,
            offset=offset
        )

    async def count_tasks(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        statuses: Optional[List[TaskStatus]] = None,
        priorities: Optional[List[TaskPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        """Count user tasks matching criteria."""
        return await self._store.count(
            user_id=user_id,
            statuses=statuses,
            priorities=priorities,
            tags=tags,
            query=query,
            include_inactive=include_inactive
        )

    async def update_task_with_status(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[TaskPriority] = None,
        due_at: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        source_commitment_id: Optional[str] = None,
        clear_commitment: bool = False
    ) -> Tuple[Optional[Task], bool]:
        """
        Atomic update returning (task, mutated).
        If the mutation is a strict no-op, mutated is False and updated_at is unchanged.
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        clean_title = title.strip() if title is not None else None
        if clean_title == "":
            raise ValueError("Task title cannot be empty.")
        target_commitment_is_valid = False
        if source_commitment_id is not None:
            commitment = await self.commitment_manager.get_commitment(source_commitment_id, user_id=user_id)
            target_commitment_is_valid = bool(commitment and commitment.is_active)

        def apply(task: Task) -> None:
            if not task.is_active:
                raise ValueError(f"Cannot update inactive/deleted task {task_id}.")
            if title is not None:
                task.title = clean_title
            if description is not None:
                task.description = description.strip()
            if priority is not None:
                task.priority = priority
            if due_at is not None:
                task.due_at = due_at
            if tags is not None:
                task.tags = tags
            if clear_commitment:
                task.source_commitment_id = None
            elif source_commitment_id is not None and source_commitment_id != task.source_commitment_id:
                if not target_commitment_is_valid:
                    raise ValueError(f"Commitment '{source_commitment_id}' does not exist or does not belong to user '{user_id}'.")
                task.source_commitment_id = source_commitment_id

        return await self._store.mutate(task_id, user_id, apply)

    async def update_task(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[TaskPriority] = None,
        due_at: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        source_commitment_id: Optional[str] = None,
        clear_commitment: bool = False,
    ) -> Optional[Task]:
        """
        Update user-editable fields of an active task.
        """
        task, _ = await self.update_task_with_status(
            task_id=task_id,
            user_id=user_id,
            title=title,
            description=description,
            priority=priority,
            due_at=due_at,
            tags=tags,
            source_commitment_id=source_commitment_id,
            clear_commitment=clear_commitment,
        )
        return task

    async def complete_task_with_status(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Tuple[Optional[Task], bool]:
        """
        Mark task completed.
        Lifecycle rule:
        - If already completed, idempotent success (mutated=False).
        - If cancelled, cannot be marked completed (raise ValueError).
        - Does NOT soft delete (is_active remains True).
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        def apply(task: Task) -> None:
            if not task.is_active:
                raise ValueError(f"Cannot complete inactive/deleted task {task_id}.")
            if task.status == TaskStatus.CANCELLED:
                raise ValueError(f"Cannot complete cancelled task {task_id}.")
            if task.status != TaskStatus.COMPLETED:
                task.status = TaskStatus.COMPLETED
                task.completed_at = datetime.now(timezone.utc)

        return await self._store.mutate(task_id, user_id, apply)

    async def start_task_with_status(self, task_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> Tuple[Optional[Task], bool]:
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        def apply(task: Task) -> None:
            if not task.is_active:
                raise ValueError(f"Cannot start inactive/deleted task {task_id}.")
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.IN_PROGRESS
            elif task.status != TaskStatus.IN_PROGRESS:
                raise ValueError(f"Cannot start {task.status.value} task {task_id}.")

        return await self._store.mutate(task_id, user_id, apply)

    async def start_task(self, task_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> Optional[Task]:
        task, _ = await self.start_task_with_status(task_id=task_id, user_id=user_id)
        return task

    async def complete_task(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Optional[Task]:
        task, _ = await self.complete_task_with_status(task_id=task_id, user_id=user_id)
        return task

    async def cancel_task_with_status(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Tuple[Optional[Task], bool]:
        """
        Mark task cancelled.
        Lifecycle rule:
        - If already cancelled, idempotent success (mutated=False).
        - If completed, cannot be cancelled (raise ValueError).
        - Does NOT soft delete (is_active remains True).
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        def apply(task: Task) -> None:
            if not task.is_active:
                raise ValueError(f"Cannot cancel inactive/deleted task {task_id}.")
            if task.status == TaskStatus.COMPLETED:
                raise ValueError(f"Cannot cancel completed task {task_id}.")
            if task.status != TaskStatus.CANCELLED:
                task.status = TaskStatus.CANCELLED

        return await self._store.mutate(task_id, user_id, apply)

    async def cancel_task(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Optional[Task]:
        task, _ = await self.cancel_task_with_status(task_id=task_id, user_id=user_id)
        return task

    async def link_task_commitment(
        self,
        task_id: str,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Tuple[Optional[Task], bool]:
        """
        Link a task to a commitment under active user_id.
        Rules:
        - Commitment must exist and belong to user_id.
        - Linking to same commitment is idempotent no-op (mutated=False).
        - Linking to different commitment overwrites and advances updated_at (mutated=True).
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        commitment = await self.commitment_manager.get_commitment(
            commitment_id=commitment_id,
            user_id=user_id
        )
        target_is_valid = bool(commitment and commitment.is_active)

        def apply(task: Task) -> None:
            if not task.is_active:
                raise ValueError(f"Cannot link inactive/deleted task {task_id}.")
            if task.source_commitment_id == commitment_id:
                return
            if not target_is_valid:
                raise ValueError(f"Commitment '{commitment_id}' does not exist or does not belong to user '{user_id}'.")
            task.source_commitment_id = commitment_id

        return await self._store.mutate(task_id, user_id, apply)

    async def unlink_task_commitment(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Tuple[Optional[Task], bool]:
        """
        Unlink a task from its commitment.
        Rules:
        - If already unlinked (source_commitment_id is None): no-op (mutated=False).
        - If was linked: clears source_commitment_id and advances updated_at (mutated=True).
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        def apply(task: Task) -> None:
            if not task.is_active:
                raise ValueError(f"Cannot unlink inactive/deleted task {task_id}.")
            task.source_commitment_id = None

        return await self._store.mutate(task_id, user_id, apply)

    async def delete_task(
        self,
        task_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        soft: bool = True
    ) -> bool:
        """
        Soft delete (is_active = False) or hard remove task.
        Does NOT automatically mark task completed or cancelled.
        """
        return await self._store.delete(task_id=task_id, user_id=user_id, soft=soft)
