from datetime import datetime, timezone
from typing import Dict, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel


class GetCurrentTimeTool(BaseTool):
    """
    Built-in deterministic tool to retrieve current date and time in UTC or a specified timezone.
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_current_time",
            description="Returns the current date and time. Optionally accepts an IANA timezone name (e.g. 'UTC', 'America/New_York', 'Asia/Kolkata', 'Europe/London').",
            parameters_schema={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone name. Defaults to UTC if omitted or invalid."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tz_name = params.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name) if tz_name else timezone.utc
            resolved_tz = tz_name or "UTC"
        except (ZoneInfoNotFoundError, ValueError):
            tz = timezone.utc
            resolved_tz = "UTC (fallback)"

        now = datetime.now(tz)
        return {
            "datetime": now.isoformat(),
            "formatted": now.strftime("%A, %B %d, %Y %I:%M:%S %p"),
            "timezone": resolved_tz,
        }
