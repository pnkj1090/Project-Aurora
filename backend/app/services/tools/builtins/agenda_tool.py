from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.agenda import (
    AgendaService,
    get_agenda_service,
    InvalidTimezoneError,
)

logger = logging.getLogger("aurora.tools.agenda")


class GetDailyAgendaTool(BaseTool):
    """
    Read-only tool retrieving a unified chronological daily agenda projection
    across Calendar, Tasks, Commitments, and Reminders.
    Risk level: LOW.
    Confirmation: False.
    Produces zero mutations and zero client actions.
    """

    def __init__(self, agenda_service: Optional[AgendaService] = None):
        self._agenda_service = agenda_service

    @property
    def service(self) -> AgendaService:
        return self._agenda_service or get_agenda_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_daily_agenda",
            description=(
                "Retrieves the unified chronological agenda for an explicit date in the user's timezone, "
                "including calendar events, task deadlines, commitment targets, scheduled reminders, "
                "and detected temporal conflicts."
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
                        "description": "Optional IANA timezone identifier (e.g. 'America/New_York', 'Europe/London')."
                    },
                    "include_conflicts": {
                        "type": "boolean",
                        "description": "Whether to evaluate and include cross-domain conflicts. Defaults to true.",
                        "default": True
                    },
                    "include_free_slots": {
                        "type": "boolean",
                        "description": "Whether to calculate available working gaps. Defaults to true.",
                        "default": True
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
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[]  # Handled dynamically; fails closed if 0 domains authorized
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
        include_conflicts = bool(params.get("include_conflicts", True))
        include_free_slots = bool(params.get("include_free_slots", True))
        work_start = int(params.get("work_start_hour", 9))
        work_end = int(params.get("work_end_hour", 18))

        try:
            agenda = await self.service.get_daily_agenda(
                user_id=user_id,
                target_date=target_date,
                explicit_tz=tz_str,
                include_conflicts=include_conflicts,
                include_free_slots=include_free_slots,
                work_start_hour=work_start,
                work_end_hour=work_end,
            )
            return {
                "status": "success",
                "agenda": agenda.model_dump(mode="json")
            }
        except InvalidTimezoneError as exc:
            return {
                "status": "error",
                "error": "INVALID_TIMEZONE",
                "message": str(exc)
            }
        except PermissionError as exc:
            return {
                "status": "error",
                "error": "PERMISSION_DENIED",
                "message": str(exc)
            }
        except ValueError as exc:
            return {
                "status": "error",
                "error": "INVALID_PARAMETERS",
                "message": str(exc)
            }
        except Exception as exc:
            logger.exception("get_daily_agenda tool failed")
            return {
                "status": "error",
                "error": "INTERNAL_ERROR",
                "message": "An error occurred while computing daily agenda."
            }


class ProposeScheduleSlotTool(BaseTool):
    """
    Read-only tool proposing deterministic feasible time slots for completing a task
    based on calendar availability and task deadline constraints.
    Risk level: LOW.
    Confirmation: False.
    Produces zero mutations and zero client actions.
    """

    def __init__(self, agenda_service: Optional[AgendaService] = None):
        self._agenda_service = agenda_service

    @property
    def service(self) -> AgendaService:
        return self._agenda_service or get_agenda_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="propose_schedule_slot",
            description=(
                "Evaluates calendar availability and task deadline constraints to propose "
                "deterministic feasible time slots for a task. Read-only analytical recommendation; "
                "does not mutate any task or schedule any reminder."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Public identifier of the task (e.g. 'tsk_123')."
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Required work duration in minutes (must be >= 15)."
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional target date string (YYYY-MM-DD)."
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
                    }
                },
                "required": ["task_id", "duration_minutes"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID

        task_id = params.get("task_id")
        if not task_id or not str(task_id).strip():
            return {
                "status": "error",
                "error": "MISSING_TASK_ID",
                "message": "Parameter 'task_id' is required."
            }

        duration_raw = params.get("duration_minutes")
        if duration_raw is None:
            return {
                "status": "error",
                "error": "MISSING_DURATION",
                "message": "Parameter 'duration_minutes' is required (must be >= 15)."
            }

        try:
            duration_minutes = int(duration_raw)
            if duration_minutes < 15:
                return {
                    "status": "error",
                    "error": "INVALID_DURATION",
                    "message": "duration_minutes must be an integer >= 15."
                }
        except (ValueError, TypeError):
            return {
                "status": "error",
                "error": "INVALID_DURATION",
                "message": "duration_minutes must be a valid integer."
            }

        date_str = params.get("date")
        tz_str = params.get("timezone")
        work_start = int(params.get("work_start_hour", 9))
        work_end = int(params.get("work_end_hour", 18))

        try:
            proposal = await self.service.propose_schedule_slot(
                user_id=user_id,
                task_id=str(task_id).strip(),
                duration_minutes=duration_minutes,
                date_str=date_str,
                explicit_tz=tz_str,
                work_start_hour=work_start,
                work_end_hour=work_end,
            )
            return {
                "status": "success",
                "proposal": proposal.model_dump(mode="json")
            }
        except InvalidTimezoneError as exc:
            return {
                "status": "error",
                "error": "INVALID_TIMEZONE",
                "message": str(exc)
            }
        except PermissionError as exc:
            return {
                "status": "error",
                "error": "PERMISSION_DENIED",
                "message": str(exc)
            }
        except ValueError as exc:
            return {
                "status": "error",
                "error": "INVALID_PARAMETERS",
                "message": str(exc)
            }
        except Exception as exc:
            logger.exception("propose_schedule_slot tool failed")
            return {
                "status": "error",
                "error": "INTERNAL_ERROR",
                "message": "An error occurred while evaluating slot proposals."
            }


class DetectScheduleConflictsTool(BaseTool):
    """
    Read-only tool scanning a rolling temporal window for cross-domain collisions,
    deadline clashes, and domain logic inconsistencies.
    Reuses AgendaService conflict engine directly.
    Risk level: LOW.
    Confirmation: False.
    Produces zero mutations and zero client actions.
    """

    def __init__(self, agenda_service: Optional[AgendaService] = None):
        self._agenda_service = agenda_service

    @property
    def service(self) -> AgendaService:
        return self._agenda_service or get_agenda_service()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="detect_schedule_conflicts",
            description=(
                "Scans a rolling temporal window (1-168 hours) to detect cross-domain collisions, "
                "meeting overlaps, deadline clashes, and domain logic inconsistencies."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "window_hours": {
                        "type": "integer",
                        "description": "Lookahead window in hours (1-168). Defaults to 24.",
                        "default": 24
                    },
                    "start_datetime": {
                        "type": "string",
                        "description": "Optional ISO 8601 start datetime with timezone offset (defaults to now)."
                    },
                    "timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[]  # Handled dynamically
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID

        window_hours = int(params.get("window_hours", 24))
        start_str = params.get("start_datetime")
        start_dt: Optional[datetime] = None
        if start_str:
            try:
                start_dt = datetime.fromisoformat(str(start_str).strip())
            except ValueError:
                return {
                    "status": "error",
                    "error": "INVALID_DATETIME",
                    "message": f"Invalid start_datetime format: '{start_str}'."
                }

        tz_str = params.get("timezone")

        try:
            scan = await self.service.detect_conflicts(
                user_id=user_id,
                window_hours=window_hours,
                start_datetime=start_dt,
                explicit_tz=tz_str,
            )
            return {
                "status": "success",
                "conflicts": scan.model_dump(mode="json")
            }
        except InvalidTimezoneError as exc:
            return {
                "status": "error",
                "error": "INVALID_TIMEZONE",
                "message": str(exc)
            }
        except PermissionError as exc:
            return {
                "status": "error",
                "error": "PERMISSION_DENIED",
                "message": str(exc)
            }
        except ValueError as exc:
            return {
                "status": "error",
                "error": "INVALID_PARAMETERS",
                "message": str(exc)
            }
        except Exception as exc:
            logger.exception("detect_schedule_conflicts tool failed")
            return {
                "status": "error",
                "error": "INTERNAL_ERROR",
                "message": "An error occurred while detecting schedule conflicts."
            }
