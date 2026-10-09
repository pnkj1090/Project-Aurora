from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.agenda.briefing import EventBriefingService, get_briefing_service

logger = logging.getLogger("aurora.tools.briefing")


class GenerateEventBriefingTool(BaseTool):
    """
    Read-only tool synthesizing contextual agenda intelligence for an upcoming calendar event.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, briefing_service: Optional[EventBriefingService] = None):
        self._briefing_service = briefing_service

    @property
    def service(self) -> EventBriefingService:
        return self._briefing_service or get_briefing_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="generate_event_briefing",
            description=(
                "Synthesizes pre-event contextual briefing for an upcoming calendar event. "
                "Aggregates attendees, relevant tasks, active commitments, and contextual memories. "
                "Read-only; produces no mutations."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "Unique identifier of the calendar event."
                    }
                },
                "required": ["event_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.CALENDAR]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        event_id = params.get("event_id", "").strip()

        if not event_id:
            return {"status": "error", "error": "MISSING_EVENT_ID", "message": "event_id is required."}

        try:
            briefing = await self.service.generate_briefing(event_id=event_id, user_id=user_id)
            return {
                "status": "success",
                "briefing": briefing.model_dump(mode="json")
            }
        except ValueError as exc:
            return {"status": "error", "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("GenerateEventBriefingTool error: %s", exc, exc_info=True)
            return {"status": "error", "error": "INTERNAL_ERROR", "message": str(exc)}


class ProposeEventDebriefTool(BaseTool):
    """
    Read-only tool generating a server-held debrief proposal from meeting notes or transcripts.
    Risk level: LOW.
    Confirmation: False (generates proposal in DRAFT state; mutations execute via apply_event_debrief).
    """

    def __init__(self, briefing_service: Optional[EventBriefingService] = None):
        self._briefing_service = briefing_service

    @property
    def service(self) -> EventBriefingService:
        return self._briefing_service or get_briefing_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="propose_event_debrief",
            description=(
                "Analyzes meeting notes or transcripts and generates a server-held debrief proposal "
                "containing proposed tasks, reminders, and episodic memories. "
                "Read-only synthesis; stores proposal in DRAFT state awaiting user confirmation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "Unique identifier of the calendar event being debriefed."
                    },
                    "raw_notes": {
                        "type": "string",
                        "description": "Meeting notes, discussion summary, or transcript text."
                    },
                    "task_candidates": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional list of action items / tasks identified."
                    },
                    "reminder_candidates": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional list of follow-up reminders."
                    },
                    "memory_candidates": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Optional list of key takeaways to store in episodic memory."
                    }
                },
                "required": ["event_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.CALENDAR]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        event_id = params.get("event_id", "").strip()
        raw_notes = params.get("raw_notes", "")
        task_cands = params.get("task_candidates")
        reminder_cands = params.get("reminder_candidates")
        memory_cands = params.get("memory_candidates")

        if not event_id:
            return {"status": "error", "error": "MISSING_EVENT_ID", "message": "event_id is required."}

        try:
            proposal = await self.service.propose_debrief(
                event_id=event_id,
                user_id=user_id,
                raw_notes=raw_notes,
                task_candidates=task_cands,
                reminder_candidates=reminder_cands,
                memory_candidates=memory_cands,
            )
            return {
                "status": "success",
                "proposal_id": proposal.proposal_id,
                "proposal_status": proposal.status.value,
                "expires_at": proposal.expires_at.isoformat(),
                "state_hash": proposal.state_hash,
                "operations_count": len(proposal.ordered_operations),
                "operations": [
                    {
                        "operation_id": op.operation_id,
                        "operation_type": op.operation_type.value,
                        "status": op.status.value,
                        "proposed_payload": op.proposed_payload,
                    }
                    for op in proposal.ordered_operations
                ]
            }
        except ValueError as exc:
            return {"status": "error", "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("ProposeEventDebriefTool error: %s", exc, exc_info=True)
            return {"status": "error", "error": "INTERNAL_ERROR", "message": str(exc)}


class ApplyEventDebriefTool(BaseTool):
    """
    Governed mutation tool to apply a server-held DebriefProposal.
    Risk level: HIGH.
    Confirmation: True (requires explicit composite confirmation via PendingConfirmation).
    Dispatches operations with Tier 1 preflight and Tier 2 per-operation validation.
    """

    def __init__(self, briefing_service: Optional[EventBriefingService] = None):
        self._briefing_service = briefing_service

    @property
    def service(self) -> EventBriefingService:
        return self._briefing_service or get_briefing_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_event_debrief",
            description=(
                "Applies a validated post-event debrief proposal after user confirmation. "
                "Executes planned tasks, reminders, and episodic memories through authoritative "
                "domain managers with preflight state-hash verification."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Unique identifier of the server-held DebriefProposal."
                    }
                },
                "required": ["proposal_id"]
            },
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
            required_permissions=[PermissionType.TASKS]  # Dynamic check during preflight
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        proposal_id = params.get("proposal_id", "").strip()

        if not proposal_id:
            return {"status": "error", "error": "MISSING_PROPOSAL_ID", "message": "proposal_id is required."}

        try:
            executed_proposal = await self.service.execute_proposal(
                proposal_id=proposal_id,
                user_id=user_id,
                approved=True,
            )

            res = {
                "status": "success" if executed_proposal.status.value == "completed" else "failed",
                "proposal_id": executed_proposal.proposal_id,
                "proposal_status": executed_proposal.status.value,
                "failure_reason": executed_proposal.failure_reason.value if executed_proposal.failure_reason else None,
                "operations": [
                    {
                        "operation_id": op.operation_id,
                        "operation_type": op.operation_type.value,
                        "status": op.status.value,
                        "created_entity_id": op.created_entity_id,
                        "failure_error": op.failure_error,
                    }
                    for op in executed_proposal.ordered_operations
                ]
            }
            return res
        except PermissionError as exc:
            return {"status": "error", "error": "INSUFFICIENT_PERMISSIONS", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("ApplyEventDebriefTool unexpected error: %s", exc, exc_info=True)
            return {"status": "error", "error": "EXECUTION_ERROR", "message": str(exc)}
