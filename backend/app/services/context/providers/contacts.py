import logging
from typing import Optional, Any, List

from app.services.context.base import BaseContextProvider, ContextBlock, ContextSensitivity
from app.services.permissions.models import PermissionType
from app.services.contacts.manager import (
    ContactManager,
    get_contact_manager,
    MAX_CONTEXT_CONTACTS,
)

logger = logging.getLogger("aurora.context.providers.contacts")


class ContactsContextProvider(BaseContextProvider):
    """
    Context provider exposing bounded, read-only contact information.
    CRITICAL ARCHITECTURAL RULES (Phase 5F Amendments):
    1. NEVER automatically inject contacts into generic system prompts.
       Without an explicit relevance/query signal in the request, returns None.
    2. Data minimization: context exposes only minimal identifying information
       (display name, organization); sensitive phone numbers and emails are
       omitted from broad context and reserved for explicit tool queries.
    3. Respects ContextAccessPolicy; only PermissionStatus.AUTHORIZED grants access.
    4. Bounded output: respects MAX_CONTEXT_CONTACTS (max 10 contacts).
    """

    def __init__(
        self,
        contact_manager: Optional[ContactManager] = None,
        max_contacts: int = MAX_CONTEXT_CONTACTS,
        priority: int = 35,
    ):
        self._manager = contact_manager
        self.max_contacts = min(max_contacts, MAX_CONTEXT_CONTACTS)
        self._priority = priority

    @property
    def manager(self) -> ContactManager:
        return self._manager or get_contact_manager()

    @property
    def name(self) -> str:
        return "contacts"

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def context_type(self) -> str:
        return "contacts"

    @property
    def source(self) -> str:
        return "ios/contacts"

    @property
    def sensitivity(self) -> ContextSensitivity:
        return ContextSensitivity.SENSITIVE

    @property
    def required_permission(self) -> Optional[PermissionType]:
        return PermissionType.CONTACTS

    def _resolve_user_id(self, session: Optional[Any] = None, **kwargs: Any) -> Optional[str]:
        if kwargs.get("user_id"):
            uid = str(kwargs["user_id"]).strip()
            if uid:
                return uid
        if session and hasattr(session, "user_id") and session.user_id:
            uid = str(session.user_id).strip()
            if uid:
                return uid
        if session and hasattr(session, "metadata") and isinstance(session.metadata, dict):
            uid = str(session.metadata.get("user_id") or "").strip()
            if uid:
                return uid
        return None

    async def get_context(
        self,
        session: Optional[Any] = None,
        **kwargs: Any
    ) -> Optional[ContextBlock]:
        """
        Assemble bounded contacts context block.
        Requires explicit relevance query (e.g., kwargs['query'] or kwargs['contact_query']).
        If called without an explicit relevance signal, returns None.
        """
        # Explicit relevance signal check
        explicit_query = kwargs.get("contact_query") or kwargs.get("query")
        if not explicit_query or not str(explicit_query).strip():
            logger.debug("ContactsContextProvider skipped: no explicit contact relevance signal provided")
            return None

        user_id = self._resolve_user_id(session, **kwargs)
        if not user_id:
            logger.warning("ContactsContextProvider: No server-controlled user identity found; failing closed.")
            return None

        # Verify permission
        is_auth = await self.manager.is_contacts_authorized(user_id=user_id)
        if not is_auth:
            logger.debug("ContactsContextProvider skipped: user '%s' lacks authorized CONTACTS permission", user_id)
            return None

        contacts, reason = await self.manager.get_contacts(
            query=str(explicit_query).strip(),
            max_results=self.max_contacts,
            user_id=user_id,
        )

        if not contacts:
            return None

        # Data minimization: format display name and organization only
        lines: List[str] = [
            f"Relevant Contacts ({len(contacts)} matched for '{str(explicit_query).strip()}'):"
        ]
        for c in contacts:
            entry = f"- {c.display_name}"
            if c.organization:
                entry += f" ({c.organization})"
            lines.append(entry)

        content = "\n".join(lines)

        return ContextBlock(
            title="Relevant Contacts",
            content=content,
            priority=self.priority,
            sensitivity=self.sensitivity,
            source=self.source,
            is_user_visible=True,
            required_permission=self.required_permission,
            metadata={"count": len(contacts), "query": str(explicit_query).strip()}
        )
