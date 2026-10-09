from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

from app.services.commitments.models import Commitment, CommitmentStatus, CommitmentPriority


class BaseCommitmentStore(ABC):
    """
    Abstract interface for commitment persistence stores.
    Supports in-memory test mocks and persistent SQLite implementations.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize storage tables or resources."""
        pass

    @abstractmethod
    async def put(self, commitment: Commitment) -> Commitment:
        """Insert or update a commitment."""
        pass

    @abstractmethod
    async def save_mutation(self, commitment: Commitment) -> tuple[Commitment, bool]:
        """
        Authoritative store-level mutation boundary.
        Returns (persisted_or_updated_commitment, changed).
        If changed is True, updated_at is strictly advanced.
        If changed is False (strict no-op), updated_at is preserved.
        """
        pass

    @abstractmethod
    async def mutate(self, commitment_id: str, user_id: str, mutation: Callable[[Commitment], None]) -> Tuple[Optional[Commitment], bool]:
        """Apply a read/validate/write mutation to the authoritative row atomically."""
        pass

    @abstractmethod
    async def get(self, commitment_id: str, user_id: str) -> Optional[Commitment]:
        """Retrieve a commitment by ID scoped to the user."""
        pass

    @abstractmethod
    async def delete(self, commitment_id: str, user_id: str, soft: bool = True) -> bool:
        """Soft delete (is_active=False) or hard remove a commitment."""
        pass

    @abstractmethod
    async def list_commitments(
        self,
        user_id: str,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Commitment]:
        """Query commitments with user scoping, filtering, search, and pagination."""
        pass

    @abstractmethod
    async def count(
        self,
        user_id: str,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        """Count commitments matching query and filters."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Clean up store resources."""
        pass
