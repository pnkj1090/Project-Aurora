import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.commitments.base import BaseCommitmentStore
from app.services.commitments.models import Commitment, CommitmentStatus, CommitmentPriority
from app.services.commitments.store import SQLiteCommitmentStore

logger = logging.getLogger(__name__)


class CommitmentManager:
    """
    Domain service for managing commitment lifecycles, user-scoped queries,
    and business rule enforcement.
    """

    def __init__(self, store: Optional[BaseCommitmentStore] = None):
        self._store = store or SQLiteCommitmentStore()

    @property
    def store(self) -> BaseCommitmentStore:
        return self._store

    async def initialize(self) -> None:
        """Ensure storage is ready."""
        await self._store.initialize()

    async def create_commitment(
        self,
        title: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        description: Optional[str] = "",
        priority: CommitmentPriority = CommitmentPriority.MEDIUM,
        target_date: Optional[datetime] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source_memory_id: Optional[str] = None
    ) -> Commitment:
        """
        Create and persist a new tracked commitment.
        Metadata and source_memory_id are internal-only domain fields.
        """
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Commitment title cannot be empty.")

        commitment = Commitment(
            user_id=user_id,
            title=clean_title,
            description=(description or "").strip(),
            status=CommitmentStatus.ACTIVE,
            priority=priority,
            target_date=target_date,
            tags=tags or [],
            metadata=metadata or {},
            is_active=True,
            source_memory_id=source_memory_id
        )
        return await self._store.put(commitment)

    async def get_commitment(self, commitment_id: str, user_id: str = DEFAULT_LOCAL_USER_ID) -> Optional[Commitment]:
        """Retrieve a commitment strictly scoped to the user."""
        return await self._store.get(commitment_id=commitment_id, user_id=user_id)

    async def list_commitments(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Commitment]:
        """
        List commitments strictly scoped to the user with optional filters and search.
        Never queries all commitments and filters afterwards.
        """
        return await self._store.list_commitments(
            user_id=user_id,
            statuses=statuses,
            priorities=priorities,
            tags=tags,
            query=query,
            include_inactive=include_inactive,
            limit=limit,
            offset=offset
        )

    async def count_commitments(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        """Count user commitments matching criteria."""
        return await self._store.count(
            user_id=user_id,
            statuses=statuses,
            priorities=priorities,
            tags=tags,
            query=query,
            include_inactive=include_inactive
        )

    async def update_commitment_with_status(
        self,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[CommitmentPriority] = None,
        target_date: Optional[datetime] = None,
        tags: Optional[List[str]] = None
    ) -> Tuple[Optional[Commitment], bool]:
        """
        Atomic update returning (commitment, mutated).
        If the mutation is a strict no-op, mutated is False and updated_at is unchanged.
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        clean_title = title.strip() if title is not None else None
        if clean_title == "":
            raise ValueError("Commitment title cannot be empty.")

        def apply(commitment: Commitment) -> None:
            if not commitment.is_active:
                raise ValueError(f"Cannot update inactive/deleted commitment {commitment_id}.")
            if title is not None:
                commitment.title = clean_title
            if description is not None:
                commitment.description = description.strip()
            if priority is not None:
                commitment.priority = priority
            if target_date is not None:
                commitment.target_date = target_date
            if tags is not None:
                commitment.tags = tags

        return await self._store.mutate(commitment_id, user_id, apply)

    async def update_commitment(
        self,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[CommitmentPriority] = None,
        target_date: Optional[datetime] = None,
        tags: Optional[List[str]] = None
    ) -> Optional[Commitment]:
        commitment, _ = await self.update_commitment_with_status(
            commitment_id=commitment_id,
            user_id=user_id,
            title=title,
            description=description,
            priority=priority,
            target_date=target_date,
            tags=tags
        )
        return commitment

    async def complete_commitment_with_status(
        self,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Tuple[Optional[Commitment], bool]:
        """
        Mark commitment completed.
        Lifecycle rule:
        - If already completed, idempotent success (mutated=False).
        - If cancelled, cannot be marked completed (raise ValueError).
        - Does NOT soft delete (is_active remains True).
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        def apply(commitment: Commitment) -> None:
            if not commitment.is_active:
                raise ValueError(f"Cannot complete inactive/deleted commitment {commitment_id}.")
            if commitment.status == CommitmentStatus.CANCELLED:
                raise ValueError(f"Cannot complete cancelled commitment {commitment_id}.")
            if commitment.status != CommitmentStatus.COMPLETED:
                commitment.status = CommitmentStatus.COMPLETED
                commitment.completed_at = datetime.now(timezone.utc)

        return await self._store.mutate(commitment_id, user_id, apply)

    async def complete_commitment(
        self,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Optional[Commitment]:
        commitment, _ = await self.complete_commitment_with_status(commitment_id=commitment_id, user_id=user_id)
        return commitment

    async def cancel_commitment_with_status(
        self,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Tuple[Optional[Commitment], bool]:
        """
        Mark commitment cancelled.
        Lifecycle rule:
        - If already cancelled, idempotent success (mutated=False).
        - If completed, cannot be cancelled (raise ValueError).
        - Does NOT soft delete (is_active remains True).
        """
        if not user_id or not str(user_id).strip():
            raise ValueError("Active user identity is required.")

        def apply(commitment: Commitment) -> None:
            if not commitment.is_active:
                raise ValueError(f"Cannot cancel inactive/deleted commitment {commitment_id}.")
            if commitment.status == CommitmentStatus.COMPLETED:
                raise ValueError(f"Cannot cancel completed commitment {commitment_id}.")
            if commitment.status != CommitmentStatus.CANCELLED:
                commitment.status = CommitmentStatus.CANCELLED

        return await self._store.mutate(commitment_id, user_id, apply)

    async def cancel_commitment(
        self,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Optional[Commitment]:
        commitment, _ = await self.cancel_commitment_with_status(commitment_id=commitment_id, user_id=user_id)
        return commitment

    async def delete_commitment(
        self,
        commitment_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        soft: bool = True
    ) -> bool:
        """
        Soft delete (is_active = False) or hard remove commitment.
        Does NOT automatically mark commitment completed or cancelled.
        """
        return await self._store.delete(commitment_id=commitment_id, user_id=user_id, soft=soft)
