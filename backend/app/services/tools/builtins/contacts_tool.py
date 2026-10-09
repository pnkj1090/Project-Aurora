import logging
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.contacts.manager import (
    ContactManager,
    get_contact_manager,
    MAX_QUERY_RESULTS,
    DEFAULT_QUERY_RESULTS,
    MAX_QUERY_LENGTH,
)

logger = logging.getLogger("aurora.tools.builtins.contacts")


class GetContactsTool(BaseTool):
    """
    Tool allowing Aurora to explicitly search and retrieve read-only contact information.
    CRITICAL PRIVACY & SECURITY CONTRACT:
    - Read-only: cannot create, modify, delete, or merge contacts.
    - Requires PermissionType.CONTACTS to be AUTHORIZED.
    - Bounded: returns up to MAX_QUERY_RESULTS (50).
    - Data minimization: returns only essential contact lookup fields.
    - Safe logging: NEVER log contact names, phone numbers, or emails.
    """

    def __init__(self, contact_manager: Optional[ContactManager] = None):
        self._manager = contact_manager

    @property
    def manager(self) -> ContactManager:
        return self._manager or get_contact_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_contacts",
            description=(
                "Search and retrieve read-only contact information (display name, organization, "
                "phone numbers, and email addresses) from the user's contacts. "
                "Requires 'contacts' permission."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search term to match against contact names, organization, phone numbers, or email addresses."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of contacts to return (1 to 50, default 10).",
                        "default": 10
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.CONTACTS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Resolve target user identity
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID

        # Defensive permission validation
        is_auth = await self.manager.is_contacts_authorized(user_id=user_id)
        if not is_auth:
            logger.warning("Tool 'get_contacts' blocked: user '%s' lacks authorized CONTACTS permission", user_id)
            return {
                "status": "error",
                "error": "Action blocked: Required permission 'contacts' is not authorized."
            }

        query = params.get("query")
        if query is not None:
            if not isinstance(query, str):
                return {
                    "status": "error",
                    "error": "Invalid arguments for 'get_contacts': query must be a string."
                }
            if len(query.strip()) > MAX_QUERY_LENGTH:
                return {
                    "status": "error",
                    "error": f"Invalid arguments for 'get_contacts': Query length exceeds maximum limit of {MAX_QUERY_LENGTH} characters."
                }

        max_results = params.get("max_results", DEFAULT_QUERY_RESULTS)
        try:
            max_results_int = int(max_results)
            if max_results_int < 1:
                return {
                    "status": "error",
                    "error": "Invalid arguments for 'get_contacts': max_results must be at least 1."
                }
        except (ValueError, TypeError):
            return {
                "status": "error",
                "error": "Invalid arguments for 'get_contacts': max_results must be an integer."
            }

        try:
            contacts, reason = await self.manager.get_contacts(
                query=query,
                max_results=max_results_int,
                user_id=user_id,
            )
        except ValueError as exc:
            return {
                "status": "error",
                "error": str(exc)
            }

        serialized = [
            {
                "contact_id": c.contact_id,
                "display_name": c.display_name,
                "phone_numbers": c.phone_numbers,
                "email_addresses": c.email_addresses,
                "organization": c.organization,
                "source": c.source,
            }
            for c in contacts
        ]

        # Safe logging: log count only, NEVER log names, phones, or emails
        logger.info("Tool 'get_contacts' executed for user '%s', returning %d contacts", user_id, len(serialized))

        return {
            "status": "success",
            "count": len(serialized),
            "contacts": serialized,
            "query": query,
        }
