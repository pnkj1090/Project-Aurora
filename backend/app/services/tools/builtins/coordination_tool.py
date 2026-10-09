from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.agenda.coordination import (
    ScheduleCoordinationService,
    get_coordination_service,
)
from app.services.agenda.service import InvalidTimezoneError

logger = logging.getLogger("aurora.tools.coordination")


class CoordinateScheduleTool(BaseTool):
    """
    Read-only analysis tool to sequence and allocate multiple tasks into open working gaps
    on a target date without modifying task deadlines (allocation != deadline).
    Risk level: LOW.
    Confirmation: False.
    Produces zero database mutations.
    """

    def __init__(self, coordination_service: Optional[ScheduleCoordinationService] = None):
        self._coordination_service = coordination_service

    @property
    def service(self) -> ScheduleCoordinationService:
        return self._coordination_service or get_coordination_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="coordinate_schedule",
            description=(
                "Coordinates multiple pending tasks across open working gaps on a specific date, "
                "taking calendar events, working hours, and task durations into account. "
                "Read-only planning tool; does not modify task deadlines or calendar entries."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Optional ISO date string (YYYY-MM-DD). Defaults to today in user's timezone."
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier."
                    },
                    "work_start_hour": {
                        "type": "integer",
                        "description": "Start of working hours (0-23). Defaults to 9.",
                        "default": 9
                    },
                    "work_end_hour": {
                        "type": "integer",
                        "description": "End of working hours (0-23). Defaults to 18.",
                        "default": 18
                    },
                    "default_duration_minutes": {
                        "type": "integer",
                        "description": "Default duration for tasks lacking explicit duration metadata (>= 15). Defaults to 30.",
                        "default": 30
                    },
                    "task_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional subset of task IDs to coordinate. If omitted, coordinates all pending tasks."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID

        date_str = params.get("date")
        target_date: Optional[date] = None
        if date_str:
            try:
                target_date = date.fromisoformat(str(date_str).strip())
            except ValueError:
                return {
                    "status": "error",
                    "error": "INVALID_DATE",
                    "message": f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD."
                }

        tz_str = params.get("timezone")
        work_start = int(params.get("work_start_hour", 9))
        work_end = int(params.get("work_end_hour", 18))
        default_dur = int(params.get("default_duration_minutes", 30))
        task_ids = params.get("task_ids")

        if work_start >= work_end:
            return {
                "status": "error",
                "error": "INVALID_WORKING_HOURS",
                "message": "work_start_hour must be strictly less than work_end_hour."
            }

        try:
            res = await self.service.coordinate_schedule(
                user_id=user_id,
                target_date=target_date,
                explicit_tz=tz_str,
                work_start_hour=work_start,
                work_end_hour=work_end,
                default_duration_minutes=default_dur,
                task_ids=task_ids,
            )
            return {
                "status": "success",
                "target_date": res.target_date,
                "timezone": res.timezone,
                "allocations": [a.model_dump(mode="json") for a in res.allocations],
                "unallocated_tasks": res.unallocated_tasks,
                "conflicts": [c.model_dump(mode="json") for c in res.conflicts],
                "notes": res.notes,
            }
        except InvalidTimezoneError as exc:
            return {"status": "error", "error": "INVALID_TIMEZONE", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "error": "INVALID_REQUEST", "message": str(exc)}
        except Exception as exc:
            logger.error("CoordinateScheduleTool unexpected error: %s", exc, exc_info=True)
            return {"status": "error", "error": "INTERNAL_ERROR", "message": str(exc)}


class ResolveConflictsTool(BaseTool):
    """
    Read-only analysis tool to evaluate schedule conflicts and generate non-executable
    strategy alternatives (Resolution, Mitigation, Information).
    Risk level: LOW.
    Confirmation: False.
    Produces zero database mutations.
    """

    def __init__(self, coordination_service: Optional[ScheduleCoordinationService] = None):
        self._coordination_service = coordination_service

    @property
    def service(self) -> ScheduleCoordinationService:
        return self._coordination_service or get_coordination_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="resolve_conflicts",
            description=(
                "Scans for cross-domain schedule collisions and generates structured, "
                "non-executable strategy options (e.g. advancing or postponing a task deadline, "
                "or creating a reminder). Read-only analysis tool."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Optional ISO date string (YYYY-MM-DD)."
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier."
                    },
                    "window_hours": {
                        "type": "integer",
                        "description": "Lookahead evaluation window in hours (1-168). Defaults to 24.",
                        "default": 24
                    },
                    "default_duration_minutes": {
                        "type": "integer",
                        "description": "Default duration for tasks lacking metadata (>= 15). Defaults to 30.",
                        "default": 30
                    },
                    "work_start_hour": {
                        "type": "integer",
                        "description": "Start of working hours (0-23). Defaults to 9.",
                        "default": 9
                    },
                    "work_end_hour": {
                        "type": "integer",
                        "description": "End of working hours (0-23). Defaults to 18.",
                        "default": 18
                    },
                    "rescheduling_requested": {
                        "type": "boolean",
                        "description": "Whether the user explicitly requested overdue task rescheduling.",
                        "default": False
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        conversation_id = params.get("_conversation_id")
        execution_id = params.get("_execution_id")

        date_str = params.get("date")
        target_date: Optional[date] = None
        if date_str:
            try:
                target_date = date.fromisoformat(str(date_str).strip())
            except ValueError:
                return {
                    "status": "error",
                    "error": "INVALID_DATE",
                    "message": f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD."
                }

        tz_str = params.get("timezone")
        window_hours = int(params.get("window_hours", 24))
        default_dur = int(params.get("default_duration_minutes", 30))
        work_start = int(params.get("work_start_hour", 9))
        work_end = int(params.get("work_end_hour", 18))
        resched_req = bool(params.get("rescheduling_requested", False))

        try:
            analysis = await self.service.analyze_conflicts_and_strategies(
                user_id=user_id,
                conversation_id=conversation_id,
                execution_id=execution_id,
                target_date=target_date,
                explicit_tz=tz_str,
                window_hours=window_hours,
                default_duration_minutes=default_dur,
                work_start_hour=work_start,
                work_end_hour=work_end,
                rescheduling_requested=resched_req,
            )
            return {
                "status": "success",
                "analysis_id": analysis.analysis_id,
                "target_date": analysis.target_date.isoformat(),
                "timezone": analysis.timezone,
                "conflicts_count": len(analysis.conflicts),
                "conflicts": [c.model_dump(mode="json") for c in analysis.conflicts],
                "strategy_options": [s.model_dump(mode="json") for s in analysis.strategy_options],
            }
        except InvalidTimezoneError as exc:
            return {"status": "error", "error": "INVALID_TIMEZONE", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "error": "INVALID_REQUEST", "message": str(exc)}
        except Exception as exc:
            logger.error("ResolveConflictsTool unexpected error: %s", exc, exc_info=True)
            return {"status": "error", "error": "INTERNAL_ERROR", "message": str(exc)}


class ApplyCoordinationProposalTool(BaseTool):
    """
    Governed mutation tool to apply a SelectedCoordinationProposal.
    Risk level: HIGH.
    Confirmation: True (requires explicit composite confirmation via PendingConfirmation).
    Dispatches operations sequentially through existing authoritative managers with two-tier validation.
    """

    def __init__(self, coordination_service: Optional[ScheduleCoordinationService] = None):
        self._coordination_service = coordination_service

    @property
    def service(self) -> ScheduleCoordinationService:
        return self._coordination_service or get_coordination_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_coordination_proposal",
            description=(
                "Applies a validated schedule coordination proposal after user confirmation. "
                "Executes planned operations through authoritative domain tools with preflight "
                "and per-operation state validation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Unique identifier of the server-held SelectedCoordinationProposal."
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
                        "failure_error": op.failure_error,
                    }
                    for op in executed_proposal.ordered_operations
                ]
            }
            return res
        except ValueError as exc:
            return {"status": "error", "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("ApplyCoordinationProposalTool unexpected error: %s", exc, exc_info=True)
            return {"status": "error", "error": "EXECUTION_ERROR", "message": str(exc)}
