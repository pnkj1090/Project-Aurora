import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.calendar import CalendarManager, get_calendar_manager
from app.services.reminders import ReminderManager, ReminderStatus, get_reminder_manager

logger = logging.getLogger("aurora.tools.calendar")


class GetUpcomingCalendarEventsTool(BaseTool):
    """
    Read-only tool allowing Aurora to query the user's calendar events within an explicit date range.
    Risk level: LOW.
    Confirmation: False (read-only query).
    Permission: Requires PermissionType.CALENDAR to be AUTHORIZED.
    """

    def __init__(self, calendar_manager: Optional[CalendarManager] = None):
        self._manager = calendar_manager

    @property
    def manager(self) -> CalendarManager:
        return self._manager or get_calendar_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_upcoming_calendar_events",
            description=(
                "Retrieves read-only calendar events for the user within an explicit date range. "
                "Requires explicit calendar authorization. Requires timezone-aware start_at and end_at."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "start_at": {
                        "type": "string",
                        "description": "ISO 8601 formatted start datetime with timezone offset (e.g., '2026-09-22T09:00:00Z')"
                    },
                    "end_at": {
                        "type": "string",
                        "description": "ISO 8601 formatted end datetime with timezone offset (e.g., '2026-09-22T18:00:00Z')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default 10, max 50)",
                        "default": 10
                    }
                },
                "required": ["start_at", "end_at"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.CALENDAR]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID

        start_str = params.get("start_at")
        end_str = params.get("end_at")
        max_results = params.get("max_results", 10)

        if not start_str or not end_str:
            return {
                "status": "error",
                "error": "Both 'start_at' and 'end_at' ISO datetime parameters are required."
            }

        try:
            start_dt = datetime.fromisoformat(str(start_str))
            end_dt = datetime.fromisoformat(str(end_str))
        except (ValueError, TypeError) as exc:
            return {
                "status": "error",
                "error": f"Invalid ISO datetime string format: {exc}"
            }

        if start_dt.tzinfo is None or end_dt.tzinfo is None:
            return {
                "status": "error",
                "error": "Datetimes must be timezone-aware (naive datetimes are not permitted)."
            }

        try:
            events, status_reason = await self.manager.get_events(
                start_at=start_dt,
                end_at=end_dt,
                max_results=int(max_results) if isinstance(max_results, (int, str)) else 10,
                user_id=user_id
            )
        except ValueError as exc:
            return {
                "status": "error",
                "error": str(exc)
            }

        if events is None:
            if status_reason == "unauthorized":
                return {
                    "status": "error",
                    "error": "Calendar access is not authorized. The user must grant calendar permission."
                }
            return {
                "status": "error",
                "error": "Calendar is currently unavailable."
            }

        serialized = [
            {
                "event_id": ev.event_id,
                "title": ev.title,
                "start_at": ev.start_at.isoformat(),
                "end_at": ev.end_at.isoformat(),
                "all_day": ev.all_day,
                "calendar_name": ev.calendar_name,
                "location": ev.location,
                "notes_available": ev.notes_available,
            }
            for ev in events
        ]

        return {
            "status": "success",
            "count": len(serialized),
            "events": serialized,
        }


class AnalyzeScheduleAvailabilityTool(BaseTool):
    """
    Cross-domain schedule analysis tool checking Calendar and Reminders.
    Supports partial analysis when only one subsystem is authorized.
    Fails closed if neither subsystem is authorized.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(
        self,
        calendar_manager: Optional[CalendarManager] = None,
        reminder_manager: Optional[ReminderManager] = None,
        permission_manager: Optional[PermissionManager] = None,
    ):
        self._calendar_manager = calendar_manager
        self._reminder_manager = reminder_manager
        self._permission_manager = permission_manager

    @property
    def calendar_manager(self) -> CalendarManager:
        return self._calendar_manager or get_calendar_manager()

    @property
    def reminder_manager(self) -> ReminderManager:
        return self._reminder_manager or get_reminder_manager()

    @property
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager or default_permission_manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="analyze_schedule_availability",
            description=(
                "Analyzes user availability across authorized Calendar events and scheduled Reminders "
                "within a specified time window. Supports partial availability if only one source is authorized. "
                "Requires start_at and end_at in ISO 8601 format."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "start_at": {
                        "type": "string",
                        "description": "ISO 8601 formatted start datetime with timezone offset (e.g. '2026-10-01T09:00:00Z')."
                    },
                    "end_at": {
                        "type": "string",
                        "description": "ISO 8601 formatted end datetime with timezone offset (e.g. '2026-10-01T17:00:00Z')."
                    },
                    "slot_duration_minutes": {
                        "type": "integer",
                        "description": "Minimum continuous duration in minutes for available slots (default 30).",
                        "default": 30
                    }
                },
                "required": ["start_at", "end_at"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[]  # Handled dynamically per subsystem
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        start_str = params.get("start_at")
        end_str = params.get("end_at")
        slot_duration = max(5, int(params.get("slot_duration_minutes", 30)))

        if not start_str or not end_str:
            return {
                "status": "error",
                "error": "Both 'start_at' and 'end_at' ISO datetime parameters are required."
            }

        try:
            start_dt = datetime.fromisoformat(str(start_str))
            end_dt = datetime.fromisoformat(str(end_str))
        except (ValueError, TypeError) as exc:
            return {
                "status": "error",
                "error": f"Invalid ISO datetime string format: {exc}"
            }

        if start_dt.tzinfo is None or end_dt.tzinfo is None:
            return {
                "status": "error",
                "error": "Datetimes must be timezone-aware (naive datetimes are not permitted)."
            }

        start_utc = start_dt.astimezone(timezone.utc)
        end_utc = end_dt.astimezone(timezone.utc)

        if start_utc >= end_utc:
            return {
                "status": "error",
                "error": "Parameter 'start_at' must be strictly before 'end_at'."
            }

        # Check permissions immediately prior to accessing each subsystem
        can_cal = await self.permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id)
        can_rem = await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)

        # Fail closed if neither subsystem is authorized
        if not can_cal and not can_rem:
            return {
                "status": "error",
                "error": "Schedule availability analysis requires authorization for at least one source (Calendar or Reminders). Both are currently unauthorized.",
                "included_sources": [],
                "omitted_sources": ["calendar", "reminders"]
            }

        included_sources = []
        omitted_sources = []
        busy_blocks: List[Dict[str, Any]] = []

        # 1. Calendar access if authorized
        events_found = 0
        if can_cal:
            included_sources.append("calendar")
            try:
                events, status_reason = await self.calendar_manager.get_events(
                    start_at=start_utc,
                    end_at=end_utc,
                    max_results=50,
                    user_id=user_id
                )
                if events:
                    events_found = len(events)
                    for ev in events:
                        ev_start = ev.start_at.astimezone(timezone.utc)
                        ev_end = ev.end_at.astimezone(timezone.utc)
                        # Clamp to window
                        block_start = max(start_utc, ev_start)
                        block_end = min(end_utc, ev_end)
                        if block_start < block_end:
                            busy_blocks.append({
                                "source": "calendar",
                                "id": ev.event_id,
                                "title": ev.title,
                                "start_at": block_start.isoformat(),
                                "end_at": block_end.isoformat(),
                                "start_dt": block_start,
                                "end_dt": block_end,
                            })
            except Exception as exc:
                logger.warning("Error fetching calendar events for availability: %s", exc)
        else:
            omitted_sources.append("calendar")

        # 2. Reminders access if authorized
        reminders_found = 0
        if can_rem:
            included_sources.append("reminders")
            try:
                reminders = await self.reminder_manager.list_reminders(
                    user_id=user_id,
                    statuses=[ReminderStatus.SCHEDULED],
                    limit=100
                )
                for r in reminders:
                    r_dt = r.scheduled_at if r.scheduled_at.tzinfo else r.scheduled_at.replace(tzinfo=timezone.utc)
                    r_utc = r_dt.astimezone(timezone.utc)
                    if start_utc <= r_utc <= end_utc:
                        reminders_found += 1
                        block_end = min(end_utc, r_utc + timedelta(minutes=15))
                        busy_blocks.append({
                            "source": "reminders",
                            "id": r.id,
                            "title": r.title,
                            "start_at": r_utc.isoformat(),
                            "end_at": block_end.isoformat(),
                            "start_dt": r_utc,
                            "end_dt": block_end,
                        })
            except Exception as exc:
                logger.warning("Error fetching reminders for availability: %s", exc)
        else:
            omitted_sources.append("reminders")

        # Sort busy intervals and merge overlaps
        busy_blocks.sort(key=lambda b: b["start_dt"])
        merged_intervals: List[Tuple[datetime, datetime]] = []
        for b in busy_blocks:
            b_s = b["start_dt"]
            b_e = b["end_dt"]
            if not merged_intervals:
                merged_intervals.append((b_s, b_e))
            else:
                last_s, last_e = merged_intervals[-1]
                if b_s <= last_e:
                    merged_intervals[-1] = (last_s, max(last_e, b_e))
                else:
                    merged_intervals.append((b_s, b_e))

        # Compute free slots between start_utc and end_utc
        free_slots: List[Dict[str, Any]] = []
        curr = start_utc
        for int_s, int_e in merged_intervals:
            if int_s > curr:
                duration_mins = int((int_s - curr).total_seconds() / 60)
                if duration_mins >= slot_duration:
                    free_slots.append({
                        "start_at": curr.isoformat(),
                        "end_at": int_s.isoformat(),
                        "duration_minutes": duration_mins
                    })
            curr = max(curr, int_e)

        if curr < end_utc:
            duration_mins = int((end_utc - curr).total_seconds() / 60)
            if duration_mins >= slot_duration:
                free_slots.append({
                    "start_at": curr.isoformat(),
                    "end_at": end_utc.isoformat(),
                    "duration_minutes": duration_mins
                })

        total_busy_mins = sum(int((e - s).total_seconds() / 60) for s, e in merged_intervals)

        # Remove internal datetime objects from serialized busy blocks
        clean_busy_blocks = [
            {
                "source": b["source"],
                "id": b["id"],
                "title": b["title"],
                "start_at": b["start_at"],
                "end_at": b["end_at"]
            }
            for b in busy_blocks
        ]

        return {
            "status": "success",
            "window_start": start_utc.isoformat(),
            "window_end": end_utc.isoformat(),
            "included_sources": included_sources,
            "omitted_sources": omitted_sources,
            "events_analyzed": events_found,
            "reminders_analyzed": reminders_found,
            "total_busy_minutes": total_busy_mins,
            "busy_blocks": clean_busy_blocks,
            "available_slots": free_slots,
        }
