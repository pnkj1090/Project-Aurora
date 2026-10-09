from __future__ import annotations

from datetime import datetime
import logging
from typing import Any, Dict, Optional

from app.services.agenda.schedule_disruption import (
    ScheduleDisruptionDiagnosticService,
    get_schedule_disruption_diagnostic_service,
)
from app.services.agenda.schedule_disruption_models import (
    DiagnoseScheduleDisruptionRequest,
    Phase6JError,
)
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel

logger = logging.getLogger("aurora.tools.schedule_disruption")


class ScheduleDisruptionDiagnosticTool(BaseTool):
    """
    Authoritative schedule disruption diagnostic tool.
    Evaluates overload severity, focus capacity deficits against working hours,
    and discovers recovery candidate tasks and reminders.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, service: Optional[ScheduleDisruptionDiagnosticService] = None):
        self._service = service

    @property
    def service(self) -> ScheduleDisruptionDiagnosticService:
        return self._service or get_schedule_disruption_diagnostic_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.LOW

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="diagnose_schedule_disruption",
            description=(
                "Diagnoses schedule disruption, evaluates focus capacity deficits against working hours, "
                "identifies tasks in jeopardy, and discovers recommended recovery candidate tasks and reminders."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_window_start": {
                        "type": "string",
                        "description": "Optional ISO-8601 UTC start of evaluation window (default: now UTC).",
                    },
                    "target_window_end": {
                        "type": "string",
                        "description": "Optional ISO-8601 UTC end of evaluation window (default: now + 48 hours).",
                    },
                    "client_preferred_timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier (e.g. 'America/New_York').",
                    },
                    "include_reminders": {
                        "type": "boolean",
                        "description": "Whether to include scheduled reminders in recovery candidate discovery (default: false).",
                    },
                },
            },
            risk_level=ToolRiskLevel.LOW,
        )

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """
        Executes disruption diagnosis with strict user scoping and parameter mapping.
        Accepts parameters via dictionary (ToolExecutor contract) or keyword arguments.
        Strictly requires authenticated user_id; zero fallback to DEFAULT_LOCAL_USER_ID.
        """
        try:
            params: Dict[str, Any] = {}
            user_id: Optional[str] = None

            if args and isinstance(args[0], dict):
                params = dict(args[0])
                user_id = params.pop("_user_id", None) or params.pop("user_id", None)
            elif args and isinstance(args[0], str):
                user_id = args[0]
                params = dict(kwargs)
            else:
                params = dict(kwargs)
                user_id = params.pop("user_id", None) or params.pop("_user_id", None)

            if not user_id or not str(user_id).strip():
                return {
                    "error": "UNAUTHORIZED",
                    "message": "Authenticated user ID must be provided.",
                }

            start_str = params.get("target_window_start")
            end_str = params.get("target_window_end")
            tz_str = params.get("client_preferred_timezone")
            include_reminders = params.get("include_reminders", False)

            start_dt: Optional[datetime] = None
            if start_str and isinstance(start_str, str):
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))

            end_dt: Optional[datetime] = None
            if end_str and isinstance(end_str, str):
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))

            req = DiagnoseScheduleDisruptionRequest(
                target_window_start=start_dt,
                target_window_end=end_dt,
                client_preferred_timezone=tz_str,
                include_reminders=bool(include_reminders),
            )

            diagnostic = await self.service.diagnose_disruption(
                authenticated_user_id=str(user_id).strip(),
                request=req,
            )
            return diagnostic.model_dump(mode="json")
        except Phase6JError as e:
            return {"error": e.error_code, "message": str(e)}
        except Exception as exc:
            logger.exception("Unexpected error in diagnose_schedule_disruption tool: %s", exc)
            return {"error": "INTERNAL_ERROR", "message": str(exc)}

