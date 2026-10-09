from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from app.services.contacts.models import ContactSnapshot
from app.services.identity.models import DEFAULT_LOCAL_USER_ID


class BaseContactManager(ABC):
    """Abstract interface for Aurora Contact Management."""

    @abstractmethod
    async def is_contacts_authorized(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> bool:
        """Verify if PermissionType.CONTACTS is AUTHORIZED for the target user."""
        pass

    @abstractmethod
    async def get_contacts(
        self,
        query: Optional[str] = None,
        max_results: int = 10,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Tuple[List[ContactSnapshot], str]:
        """
        Retrieve read-only contacts for the user matching an optional search query.
        Returns (contacts, reason_status).
        """
        pass

    @abstractmethod
    async def set_contacts(
        self,
        contacts: List[ContactSnapshot],
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> List[ContactSnapshot]:
        """
        Ingest a bounded ephemeral contact snapshot for the target user.
        Replaces any prior snapshot. Does not retain historical data or persist to disk.
        """
        pass

    @abstractmethod
    def clear_contacts(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> None:
        """Clear ephemeral contact snapshot for the target user."""
        pass
