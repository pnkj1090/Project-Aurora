import logging
from typing import Dict, List, Optional, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.contacts.base import BaseContactManager
from app.services.contacts.models import ContactSnapshot

logger = logging.getLogger("aurora.contacts.manager")

MAX_SNAPSHOT_CONTACTS = 100
MAX_QUERY_RESULTS = 50
DEFAULT_QUERY_RESULTS = 10
MAX_CONTEXT_CONTACTS = 10
MAX_QUERY_LENGTH = 100


class ContactManager(BaseContactManager):
    """
    ContactManager managing bounded, read-only contact information.
    CRITICAL ARCHITECTURAL RULES:
    - Zero contact mutation: contacts cannot be created, edited, deleted, or merged.
    - Zero persistence: no SQLite tables, no disk persistence; in-memory ephemeral cache only.
    - User isolation: all operations strictly scoped by authenticated user_id.
    - Safe logging: NEVER log contact names, phone numbers, email addresses, or addresses.
    """

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self._permission_manager = permission_manager or default_permission_manager
        # Ephemeral in-memory store: user_id -> List[ContactSnapshot]
        self._contacts: Dict[str, List[ContactSnapshot]] = {}

    @property
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager

    async def is_contacts_authorized(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> bool:
        """Verify if PermissionType.CONTACTS is AUTHORIZED for the active user."""
        return await self._permission_manager.is_authorized(
            PermissionType.CONTACTS,
            user_id=user_id
        )

    async def set_contacts(
        self,
        contacts: List[ContactSnapshot],
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> List[ContactSnapshot]:
        """
        Ingest a bounded ephemeral contact snapshot relevant to the current explicit request.
        Replaces any prior snapshot. Does not retain historical data or persist to disk.
        """
        is_auth = await self.is_contacts_authorized(user_id=user_id)
        if not is_auth:
            logger.warning("Contact ingestion rejected: user '%s' lacks authorized CONTACTS permission", user_id)
            raise PermissionError(f"Permission '{PermissionType.CONTACTS.value}' is not authorized for user '{user_id}'.")

        if len(contacts) > MAX_SNAPSHOT_CONTACTS:
            raise ValueError(f"Snapshot exceeds maximum allowed contacts limit of {MAX_SNAPSHOT_CONTACTS} (received {len(contacts)}).")

        # Check for duplicate contact IDs within this snapshot
        seen_ids = set()
        for c in contacts:
            if c.contact_id in seen_ids:
                raise ValueError(f"Duplicate contact ID '{c.contact_id}' detected in snapshot. Snapshot rejected.")
            seen_ids.add(c.contact_id)

        # Replace previous snapshot in memory for this user
        cleaned: List[ContactSnapshot] = []
        for c in contacts:
            scoped = c.model_copy(update={"user_id": user_id})
            cleaned.append(scoped)

        self._contacts[user_id] = cleaned
        # Safe logging: log count only, NEVER log contact names, emails, or phone numbers
        logger.info("Updated ephemeral contact snapshot for user '%s' with %d contacts", user_id, len(cleaned))
        return cleaned

    async def get_contacts(
        self,
        query: Optional[str] = None,
        max_results: int = DEFAULT_QUERY_RESULTS,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Tuple[List[ContactSnapshot], str]:
        """
        Retrieve read-only contacts for the user matching an optional search query.
        Matches against display_name, organization, phone_numbers, and email_addresses.
        Returns (contacts, reason_status).
        """
        is_auth = await self.is_contacts_authorized(user_id=user_id)
        if not is_auth:
            logger.debug("Contacts retrieval blocked: user '%s' lacks authorized CONTACTS permission", user_id)
            return [], "unauthorized"

        limit = max(1, min(max_results, MAX_QUERY_RESULTS))
        all_contacts = self._contacts.get(user_id, [])

        # Query normalization
        norm_query = None
        if query is not None:
            trimmed = query.strip()
            if len(trimmed) > MAX_QUERY_LENGTH:
                raise ValueError(f"Query length exceeds maximum limit of {MAX_QUERY_LENGTH} characters.")
            if trimmed:
                norm_query = trimmed.lower()

        filtered: List[ContactSnapshot] = []
        for c in all_contacts:
            if norm_query is None:
                filtered.append(c.model_copy(deep=True))
            else:
                name_match = norm_query in c.display_name.lower()
                org_match = c.organization is not None and norm_query in c.organization.lower()
                phone_match = any(norm_query in p.lower() for p in c.phone_numbers)
                email_match = any(norm_query in e.lower() for e in c.email_addresses)
                if name_match or org_match or phone_match or email_match:
                    filtered.append(c.model_copy(deep=True))

        # Deterministic chronological/lexicographical order
        filtered.sort(key=lambda item: (item.display_name.lower(), item.contact_id))

        # Safe logging: log match count only
        logger.debug("Contacts search for user '%s' returned %d results", user_id, min(len(filtered), limit))
        return filtered[:limit], "available"

    def clear_contacts(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> None:
        """Clear ephemeral contact snapshot for the target user."""
        self._contacts.pop(user_id, None)
        logger.info("Cleared ephemeral contacts for user '%s'", user_id)


# Global singleton instance
_default_contact_manager: Optional[ContactManager] = None


def get_contact_manager(
    permission_manager: Optional[PermissionManager] = None
) -> ContactManager:
    """Retrieve or construct the ContactManager instance."""
    global _default_contact_manager
    if permission_manager is not None:
        return ContactManager(permission_manager=permission_manager)
    if _default_contact_manager is None:
        _default_contact_manager = ContactManager()
    return _default_contact_manager


def get_contact_manager_dependency() -> ContactManager:
    """FastAPI dependency provider."""
    return get_contact_manager()
