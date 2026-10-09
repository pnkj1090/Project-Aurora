from app.services.contacts.models import ContactSnapshot
from app.services.contacts.base import BaseContactManager
from app.services.contacts.manager import (
    ContactManager,
    get_contact_manager,
    get_contact_manager_dependency,
    MAX_SNAPSHOT_CONTACTS,
    MAX_QUERY_RESULTS,
    DEFAULT_QUERY_RESULTS,
    MAX_CONTEXT_CONTACTS,
    MAX_QUERY_LENGTH,
)

__all__ = [
    "ContactSnapshot",
    "BaseContactManager",
    "ContactManager",
    "get_contact_manager",
    "get_contact_manager_dependency",
    "MAX_SNAPSHOT_CONTACTS",
    "MAX_QUERY_RESULTS",
    "DEFAULT_QUERY_RESULTS",
    "MAX_CONTEXT_CONTACTS",
    "MAX_QUERY_LENGTH",
]
