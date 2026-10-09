"""
search_web tool implementation for Project Aurora.
Read-only, LOW-risk tool enabling public web searches via SafeWebReaderService.
"""
import logging
from typing import Any, Dict, Optional

from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.web.privacy.models import OutboundPrivacyViolationError
from app.services.web.reader.service import SafeWebReaderService
from app.services.web.security.exceptions import WebSecurityError

logger = logging.getLogger("aurora.tools.web.search")


class SearchWebTool(BaseTool):
    """
    Assistant tool to search the public web for current information, documentation, or public facts.
    """

    def __init__(self, reader_service: Optional[SafeWebReaderService] = None):
        self.reader_service = reader_service or SafeWebReaderService()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_web",
            description="Search the public web for current information, public facts, documentation, or current events. Returns top search results with title, URL, and snippet.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query for public web information."
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to retrieve (1 to 5). Defaults to 3.",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 5
                    }
                },
                "required": ["query"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes search via the SafeWebReaderService with server-controlled user identity.
        """
        # Identity strictly extracted from server-injected execution context
        user_id = params.get("_user_id")
        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            logger.warning("search_web invocation missing server-controlled user identity context.")
            return {
                "success": False,
                "error": "Action blocked: User identity context is required."
            }

        query = params.get("query")
        if not query or not isinstance(query, str) or not query.strip():
            return {
                "success": False,
                "error": "Parameter 'query' must be a non-empty string."
            }

        max_results_val = params.get("max_results", 3)
        try:
            max_results = int(max_results_val)
        except (ValueError, TypeError):
            return {
                "success": False,
                "error": "Parameter 'max_results' must be a valid integer."
            }

        if max_results < 1 or max_results > 5:
            return {
                "success": False,
                "error": f"Parameter 'max_results' must be between 1 and 5 (got {max_results})."
            }

        try:
            result = await self.reader_service.search(
                query=query.strip(),
                user_id=user_id.strip(),
                max_results=max_results,
            )
            return {
                "success": True,
                "query": result.query,
                "results": [item.model_dump() for item in result.results],
                "total_results": result.total_results,
                "cached": result.cached,
            }
        except OutboundPrivacyViolationError as pve:
            logger.warning("search_web rejected by privacy boundary: %s", pve)
            return {
                "success": False,
                "error": f"Search query rejected: {str(pve)}"
            }
        except WebSecurityError as wse:
            logger.warning("search_web rejected by security policy: %s", wse)
            return {
                "success": False,
                "error": f"Search operation failed: {str(wse)}"
            }
        except Exception as exc:
            logger.exception("Unexpected error in search_web tool: %s", exc)
            return {
                "success": False,
                "error": "Search operation encountered an internal error. Please refine your query."
            }
