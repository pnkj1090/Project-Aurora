import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.schemas import ToolCall, PendingConfirmation
from app.services.reminders import ReminderManager, ReminderStatus, get_reminder_manager
from app.services.reminders.models import Reminder

logger = logging.getLogger("aurora.tools.reminders")


def build_client_action(action: str, reminder: Reminder) -> Dict[str, Any]:
    """
    Constructs a deterministic, canonical client notification action.
    Action ID format: [action]:[reminder_id]:[canonical UTC updated_at with microsecond precision].
    """
    dt_updated = reminder.updated_at
    if dt_updated.tzinfo is None:
        dt_updated = dt_updated.replace(tzinfo=timezone.utc)
    else:
        dt_updated = dt_updated.astimezone(timezone.utc)
    canonical_updated_at = dt_updated.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    dt_scheduled = reminder.scheduled_at
    if dt_scheduled.tzinfo is None:
        dt_scheduled = dt_scheduled.replace(tzinfo=timezone.utc)
    else:
        dt_scheduled = dt_scheduled.astimezone(timezone.utc)
    canonical_scheduled_at = dt_scheduled.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    action_id = f"{action}:{reminder.id}:{canonical_updated_at}"

    return {
        "action_id": action_id,
        "action": action,
        "reminder_id": reminder.id,
        "notification_id": reminder.notification_id or reminder.id,
        "title": reminder.title,
        "body": reminder.body or "",
        "scheduled_at": canonical_scheduled_at,
        "updated_at": canonical_updated_at,
    }


class CreateTestReminderTool(BaseTool):
    """
    Simulated reminder tool demonstrating MEDIUM risk and mandatory user confirmation.
    Retained for backward compatibility with pre-existing tests.
    """

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="create_test_reminder",
            description="Schedules a simulated reminder for the user. Requires user confirmation before execution.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title or subject of the reminder."
                    },
                    "datetime": {
                        "type": "string",
                        "description": "Target date and time for the reminder (e.g. '2026-09-22 09:00 AM' or ISO format)."
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional additional notes or context."
                    }
                },
                "required": ["title", "datetime"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=["reminders:write"]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        title = params.get("title")
        dt_str = params.get("datetime")
        notes = params.get("notes", "")

        if not title or not dt_str:
            return {
                "status": "error",
                "error": "Both 'title' and 'datetime' parameters are required."
            }

        reminder_id = f"rem_{uuid.uuid4().hex[:8]}"
        return {
            "reminder_id": reminder_id,
            "title": title,
            "scheduled_time": dt_str,
            "notes": notes,
            "status": "scheduled",
            "message": f"Successfully scheduled reminder '{title}' for {dt_str}."
        }


class CreateReminderTool(BaseTool):
    """
    Tool allowing Aurora to explicitly schedule a reminder for the user.
    Risk level: MEDIUM.
    Confirmation: True (requires explicit user consent).
    Permission: Requires PermissionType.NOTIFICATIONS to be AUTHORIZED.
    """

    def __init__(self, reminder_manager: Optional[ReminderManager] = None):
        self._manager = reminder_manager

    @property
    def manager(self) -> ReminderManager:
        return self._manager or get_reminder_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="create_reminder",
            description=(
                "Schedules an explicit reminder for the user. Requires an explicit ISO 8601 target datetime. "
                "Requires user confirmation and notification authorization."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Clear title for the reminder."
                    },
                    "scheduled_at": {
                        "type": "string",
                        "description": "Target ISO 8601 datetime string in UTC (e.g. '2026-10-01T15:00:00Z')."
                    },
                    "body": {
                        "type": "string",
                        "description": "Optional details or message notes."
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Optional linked task ID (cannot be combined with commitment_id)."
                    },
                    "commitment_id": {
                        "type": "string",
                        "description": "Optional linked commitment ID (cannot be combined with task_id)."
                    }
                },
                "required": ["title", "scheduled_at"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.NOTIFICATIONS]
        )

    def prepare_confirmation(self, tool_call: ToolCall) -> PendingConfirmation:
        args = dict(tool_call.arguments or {})
        title = args.get("title", "").strip()
        scheduled_at = args.get("scheduled_at", "").strip()
        desc = f"Schedule reminder '{title}' for {scheduled_at}"

        return PendingConfirmation(
            tool_call=tool_call,
            risk_level=ToolRiskLevel.MEDIUM,
            description=desc
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        title = params.get("title", "").strip()
        if not title:
            return {"status": "error", "error": "Parameter 'title' is required and cannot be empty."}

        raw_dt = params.get("scheduled_at")
        if not raw_dt or not isinstance(raw_dt, str) or not raw_dt.strip():
            return {
                "status": "error",
                "error": "Parameter 'scheduled_at' is required with an explicit ISO 8601 datetime format."
            }

        try:
            scheduled_at = datetime.fromisoformat(raw_dt.strip())
        except ValueError:
            return {
                "status": "error",
                "error": f"Invalid ISO datetime format for 'scheduled_at': '{raw_dt}'. Please provide a valid ISO 8601 datetime."
            }

        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        body = params.get("body", "")
        task_id = params.get("task_id")
        commitment_id = params.get("commitment_id")

        try:
            reminder = await self.manager.create_reminder(
                title=title,
                scheduled_at=scheduled_at,
                user_id=user_id,
                body=body,
                task_id=task_id,
                commitment_id=commitment_id,
            )

            client_action = build_client_action("schedule_local_notification", reminder)

            return {
                "status": "success",
                "reminder_id": reminder.id,
                "title": reminder.title,
                "scheduled_at": reminder.scheduled_at.isoformat(),
                "status_value": reminder.status.value,
                "task_id": reminder.task_id,
                "commitment_id": reminder.commitment_id,
                "client_action": client_action,
                "message": f"Successfully scheduled reminder '{reminder.title}' for {reminder.scheduled_at.isoformat()}."
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class ListRemindersTool(BaseTool):
    """
    Tool allowing Aurora to list active or scheduled reminders for the current user.
    Risk level: LOW.
    Confirmation: False (read-only query).
    """

    def __init__(self, reminder_manager: Optional[ReminderManager] = None):
        self._manager = reminder_manager

    @property
    def manager(self) -> ReminderManager:
        return self._manager or get_reminder_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_reminders",
            description="Lists scheduled or active reminders for the current user.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["scheduled", "delivered", "cancelled", "failed"],
                        "description": "Optional filter by reminder status."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of reminders to return (default 10).",
                        "default": 10
                    }
                }
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.REMINDERS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        statuses = None
        if params.get("status"):
            try:
                statuses = [ReminderStatus(params["status"].lower())]
            except ValueError:
                pass

        limit = min(50, max(1, params.get("limit", 10)))

        reminders = await self.manager.list_reminders(
            user_id=user_id,
            statuses=statuses,
            include_inactive=False,
            limit=limit
        )

        formatted = [
            {
                "id": r.id,
                "title": r.title,
                "body": r.body,
                "scheduled_at": r.scheduled_at.isoformat(),
                "status": r.status.value,
                "task_id": r.task_id,
                "commitment_id": r.commitment_id,
            }
            for r in reminders
        ]

        return {
            "status": "success",
            "count": len(formatted),
            "reminders": formatted
        }


class UpdateReminderTool(BaseTool):
    """
    Tool allowing Aurora to update a scheduled reminder.
    Risk level: MEDIUM.
    Confirmation: True (requires explicit confirmation).
    Permission: Requires PermissionType.REMINDERS to be AUTHORIZED.
    """

    def __init__(self, reminder_manager: Optional[ReminderManager] = None):
        self._manager = reminder_manager

    @property
    def manager(self) -> ReminderManager:
        return self._manager or get_reminder_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="update_reminder",
            description="Updates title, body, schedule, or item links of an existing reminder.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": "ID of reminder to update."
                    },
                    "title": {
                        "type": "string",
                        "description": "New title for the reminder."
                    },
                    "body": {
                        "type": "string",
                        "description": "New body text or notes for the reminder."
                    },
                    "scheduled_at": {
                        "type": "string",
                        "description": "New target ISO 8601 datetime string in UTC."
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID to link (cannot be combined with commitment_id)."
                    },
                    "commitment_id": {
                        "type": "string",
                        "description": "Commitment ID to link (cannot be combined with task_id)."
                    },
                    "clear_task": {
                        "type": "boolean",
                        "description": "Clear existing task link."
                    },
                    "clear_commitment": {
                        "type": "boolean",
                        "description": "Clear existing commitment link."
                    }
                },
                "required": ["reminder_id"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.REMINDERS]
        )

    def prepare_confirmation(self, tool_call: ToolCall) -> PendingConfirmation:
        args = dict(tool_call.arguments or {})
        reminder_id = args.get("reminder_id", "").strip()
        title = args.get("title")
        scheduled_at = args.get("scheduled_at")
        parts = [f"Update reminder '{reminder_id}'"]
        if title:
            parts.append(f"title to '{title}'")
        if scheduled_at:
            parts.append(f"time to {scheduled_at}")
        desc = ", ".join(parts)
        return PendingConfirmation(
            tool_call=tool_call,
            risk_level=ToolRiskLevel.MEDIUM,
            description=desc
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        reminder_id = params.get("reminder_id", "").strip()
        if not reminder_id:
            return {"status": "error", "error": "Parameter 'reminder_id' is required."}

        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        scheduled_at = None
        raw_dt = params.get("scheduled_at")
        if raw_dt is not None:
            if not isinstance(raw_dt, str) or not raw_dt.strip():
                return {"status": "error", "error": "Invalid scheduled_at parameter."}
            try:
                scheduled_at = datetime.fromisoformat(raw_dt.strip())
            except ValueError:
                return {"status": "error", "error": f"Invalid ISO datetime format for 'scheduled_at': '{raw_dt}'."}

        title = params.get("title")
        body = params.get("body")
        task_id = params.get("task_id")
        commitment_id = params.get("commitment_id")
        clear_task = bool(params.get("clear_task", False))
        clear_commitment = bool(params.get("clear_commitment", False))

        existing = await self.manager.get_reminder(reminder_id=reminder_id, user_id=user_id)
        if existing is None:
            return {"status": "error", "error": f"Reminder '{reminder_id}' not found."}

        try:
            saved, mutated = await self.manager.update_reminder_with_status(
                reminder_id=reminder_id,
                user_id=user_id,
                title=title,
                body=body,
                scheduled_at=scheduled_at,
                task_id=task_id,
                commitment_id=commitment_id,
                clear_task=clear_task,
                clear_commitment=clear_commitment,
            )

            if not saved:
                return {"status": "error", "error": f"Reminder '{reminder_id}' not found."}

            client_action = None
            if mutated:
                # Check if notification-affecting fields changed
                orig_sched = existing.scheduled_at if existing.scheduled_at.tzinfo else existing.scheduled_at.replace(tzinfo=timezone.utc)
                new_sched = saved.scheduled_at if saved.scheduled_at.tzinfo else saved.scheduled_at.replace(tzinfo=timezone.utc)
                schedule_changed = (orig_sched != new_sched)
                title_changed = (existing.title != saved.title)
                body_changed = ((existing.body or "") != (saved.body or ""))

                if schedule_changed or title_changed or body_changed:
                    client_action = build_client_action("reschedule_local_notification", saved)

            return {
                "status": "success",
                "mutated": mutated,
                "reminder_id": saved.id,
                "title": saved.title,
                "body": saved.body,
                "scheduled_at": saved.scheduled_at.isoformat(),
                "status_value": saved.status.value,
                "task_id": saved.task_id,
                "commitment_id": saved.commitment_id,
                "client_action": client_action,
                "message": f"Successfully updated reminder '{saved.title}'." if mutated else "No changes were needed for reminder."
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class SnoozeReminderTool(BaseTool):
    """
    Tool allowing Aurora to snooze a scheduled or delivered reminder forward.
    Risk level: LOW.
    Confirmation: False.
    Permission: Requires PermissionType.REMINDERS to be AUTHORIZED.
    """

    def __init__(self, reminder_manager: Optional[ReminderManager] = None):
        self._manager = reminder_manager

    @property
    def manager(self) -> ReminderManager:
        return self._manager or get_reminder_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="snooze_reminder",
            description="Snoozes an existing reminder forward deterministically by duration (default 15 minutes, bounds 5–1440m).",
            parameters_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": "ID of reminder to snooze."
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Minutes to snooze forward (5 to 1440, default 15).",
                        "default": 15
                    }
                },
                "required": ["reminder_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.REMINDERS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        reminder_id = params.get("reminder_id", "").strip()
        if not reminder_id:
            return {"status": "error", "error": "Parameter 'reminder_id' is required."}

        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        duration_minutes = int(params.get("duration_minutes", 15))

        try:
            saved, mutated = await self.manager.snooze_reminder(
                reminder_id=reminder_id,
                user_id=user_id,
                duration_minutes=duration_minutes
            )

            client_action = None
            if mutated:
                client_action = build_client_action("reschedule_local_notification", saved)

            return {
                "status": "success",
                "mutated": mutated,
                "reminder_id": saved.id,
                "title": saved.title,
                "scheduled_at": saved.scheduled_at.isoformat(),
                "status_value": saved.status.value,
                "client_action": client_action,
                "message": f"Successfully snoozed reminder '{saved.title}' to {saved.scheduled_at.isoformat()}."
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class CancelReminderTool(BaseTool):
    """
    Tool allowing Aurora to cancel a scheduled reminder.
    Risk level: MEDIUM.
    Confirmation: True (requires explicit confirmation).
    Permission: Requires PermissionType.REMINDERS to be AUTHORIZED.
    """

    def __init__(self, reminder_manager: Optional[ReminderManager] = None):
        self._manager = reminder_manager

    @property
    def manager(self) -> ReminderManager:
        return self._manager or get_reminder_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="cancel_reminder",
            description="Cancels an existing scheduled reminder by ID. Requires user confirmation.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": "Unique ID of the reminder to cancel."
                    }
                },
                "required": ["reminder_id"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.REMINDERS]
        )

    def prepare_confirmation(self, tool_call: ToolCall) -> PendingConfirmation:
        args = dict(tool_call.arguments or {})
        reminder_id = args.get("reminder_id", "").strip()
        desc = f"Cancel reminder with ID '{reminder_id}'"

        return PendingConfirmation(
            tool_call=tool_call,
            risk_level=ToolRiskLevel.MEDIUM,
            description=desc
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        reminder_id = params.get("reminder_id", "").strip()
        if not reminder_id:
            return {"status": "error", "error": "Parameter 'reminder_id' is required."}

        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        try:
            reminder, mutated = await self.manager.cancel_reminder_with_status(reminder_id=reminder_id, user_id=user_id)
            if not reminder:
                return {"status": "error", "error": f"Reminder '{reminder_id}' not found."}

            client_action = None
            if mutated:
                client_action = build_client_action("cancel_local_notification", reminder)

            return {
                "status": "success",
                "mutated": mutated,
                "reminder_id": reminder.id,
                "title": reminder.title,
                "status_value": reminder.status.value,
                "client_action": client_action,
                "message": f"Cancelled reminder '{reminder.title}'." if mutated else f"Reminder '{reminder.title}' was already cancelled."
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class LinkReminderToItemTool(BaseTool):
    """
    Tool allowing Aurora to link a reminder to an active task or commitment.
    Risk level: LOW.
    Confirmation: False.
    Permission: Requires PermissionType.REMINDERS to be AUTHORIZED.
    """

    def __init__(self, reminder_manager: Optional[ReminderManager] = None):
        self._manager = reminder_manager

    @property
    def manager(self) -> ReminderManager:
        return self._manager or get_reminder_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="link_reminder_to_item",
            description="Links an existing reminder to an active task OR commitment (mutually exclusive).",
            parameters_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": "ID of reminder to link."
                    },
                    "task_id": {
                        "type": "string",
                        "description": "Task ID to link (cannot be combined with commitment_id)."
                    },
                    "commitment_id": {
                        "type": "string",
                        "description": "Commitment ID to link (cannot be combined with task_id)."
                    }
                },
                "required": ["reminder_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.REMINDERS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        reminder_id = params.get("reminder_id", "").strip()
        if not reminder_id:
            return {"status": "error", "error": "Parameter 'reminder_id' is required."}

        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        task_id = params.get("task_id")
        commitment_id = params.get("commitment_id")

        try:
            saved, mutated = await self.manager.link_reminder(
                reminder_id=reminder_id,
                user_id=user_id,
                task_id=task_id,
                commitment_id=commitment_id
            )
            # Metadata-only linking emits zero notification client action
            return {
                "status": "success",
                "mutated": mutated,
                "reminder_id": saved.id,
                "task_id": saved.task_id,
                "commitment_id": saved.commitment_id,
                "client_action": None,
                "message": f"Successfully linked reminder '{saved.title}'." if mutated else "Reminder was already linked to requested item."
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class UnlinkReminderFromItemTool(BaseTool):
    """
    Tool allowing Aurora to unlink a reminder from its linked task or commitment.
    Risk level: LOW.
    Confirmation: False.
    Permission: Requires PermissionType.REMINDERS to be AUTHORIZED.
    """

    def __init__(self, reminder_manager: Optional[ReminderManager] = None):
        self._manager = reminder_manager

    @property
    def manager(self) -> ReminderManager:
        return self._manager or get_reminder_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="unlink_reminder_from_item",
            description="Unlinks a reminder from its associated task or commitment.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "string",
                        "description": "ID of reminder to unlink."
                    },
                    "clear_task": {
                        "type": "boolean",
                        "description": "Clear linked task.",
                        "default": True
                    },
                    "clear_commitment": {
                        "type": "boolean",
                        "description": "Clear linked commitment.",
                        "default": True
                    }
                },
                "required": ["reminder_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.REMINDERS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        reminder_id = params.get("reminder_id", "").strip()
        if not reminder_id:
            return {"status": "error", "error": "Parameter 'reminder_id' is required."}

        user_id = params.get("_user_id") or params.get("user_id")
        if not user_id:
            return {"status": "error", "error": "Server-resolved user identity is required."}

        clear_task = params.get("clear_task", True)
        clear_commitment = params.get("clear_commitment", True)

        try:
            saved, mutated = await self.manager.unlink_reminder(
                reminder_id=reminder_id,
                user_id=user_id,
                clear_task=clear_task,
                clear_commitment=clear_commitment
            )
            # Metadata-only unlinking emits zero notification client action
            return {
                "status": "success",
                "mutated": mutated,
                "reminder_id": saved.id,
                "task_id": saved.task_id,
                "commitment_id": saved.commitment_id,
                "client_action": None,
                "message": f"Successfully unlinked reminder '{saved.title}'." if mutated else "Reminder had no active links to unlink."
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
