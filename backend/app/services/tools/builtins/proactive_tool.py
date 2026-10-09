"""
Phase 5H — Proactive assistance tools.

Tools:
- ListProactiveCandidatesTool: Low risk, no confirmation, read-only list of eligible proposals.
- DismissProactiveCandidateTool: Low risk, no confirmation, dismisses a proposal.

Invariants:
- Zero leakage of internal metadata or reference_id.
- No arbitrary proactive candidate creation tool.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.proactive.manager import ProactiveManager, get_proactive_manager
from app.services.proactive.models import ProactiveStatus
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel

logger = logging.getLogger("aurora.tools.builtins.proactive")


class ListProactiveCandidatesTool(BaseTool):
    """
    Tool allowing Aurora to inspect active, eligible proactive suggestions for the user.
    Risk level: LOW.
    Confirmation: False (read-only query).
    """

    def __init__(self, proactive_manager: Optional[ProactiveManager] = None) -> None:
        self._manager = proactive_manager

    @property
    def manager(self) -> ProactiveManager:
        return self._manager or get_proactive_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_proactive_candidates",
            description="Lists active, eligible proactive suggestions or proposals for the user.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["eligible", "pending", "dismissed", "cancelled"],
                        "description": "Optional status filter. Defaults to 'eligible'.",
                        "default": "eligible",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Maximum candidates to return (1-50). Defaults to 20.",
                        "default": 20,
                    },
                },
                "required": [],
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID

        raw_status = params.get("status", "eligible")
        status_filter: Optional[ProactiveStatus] = None
        if raw_status:
            try:
                status_filter = ProactiveStatus(raw_status.lower())
            except ValueError:
                status_filter = ProactiveStatus.ELIGIBLE

        limit = min(50, max(1, params.get("limit", 20)))

        candidates, total = await self.manager.list_candidates(
            user_id=user_id,
            status=status_filter,
            limit=limit,
            offset=0,
            include_expired=False,
        )

        formatted: List[Dict[str, Any]] = [
            {
                "candidate_id": c.candidate_id,
                "trigger": c.trigger.value,
                "title": c.title,
                "message": c.message,
                "priority": c.priority.value,
                "status": c.status.value,
                "created_at": c.created_at.isoformat(),
                "expires_at": c.expires_at.isoformat(),
                "source": c.source,
                "action": c.action.value,
            }
            for c in candidates
        ]

        return {
            "status": "success",
            "count": len(formatted),
            "total": total,
            "candidates": formatted,
        }


class DismissProactiveCandidateTool(BaseTool):
    """
    Tool allowing Aurora to dismiss a proactive suggestion on the user's behalf.
    Risk level: LOW.
    Confirmation: False (non-destructive forward-progress lifecycle progression;
    does not delete or modify user tasks, reminders, or calendar events).
    """

    def __init__(self, proactive_manager: Optional[ProactiveManager] = None) -> None:
        self._manager = proactive_manager

    @property
    def manager(self) -> ProactiveManager:
        return self._manager or get_proactive_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="dismiss_proactive_candidate",
            description="Dismisses a proactive proposal by its candidate ID.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "description": "Unique ID of the proactive candidate to dismiss.",
                    },
                },
                "required": ["candidate_id"],
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        candidate_id = params.get("candidate_id", "").strip()

        if not candidate_id:
            return {"status": "error", "error": "Parameter 'candidate_id' is required."}

        candidate = await self.manager.dismiss_candidate(candidate_id=candidate_id, user_id=user_id)
        if not candidate:
            return {
                "status": "error",
                "error": f"Proactive candidate '{candidate_id}' not found or not accessible.",
            }

        return {
            "status": "success",
            "candidate_id": candidate.candidate_id,
            "title": candidate.title,
            "candidate_status": candidate.status.value,
            "message": f"Dismissed proactive suggestion '{candidate.title}'.",
        }
