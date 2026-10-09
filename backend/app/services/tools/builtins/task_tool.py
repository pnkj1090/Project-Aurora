import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.schemas import ToolCall, PendingConfirmation
from app.services.tasks import get_task_manager, TaskManager, TaskStatus, TaskPriority, Task

logger = logging.getLogger("aurora.tools.tasks")


def build_task_client_action(action: str, task: Task) -> Dict[str, Any]:
    """
    Constructs a deterministic, canonical task synchronization client action.
    Action ID format: [action]:[task_id]:[canonical UTC updated_at with microsecond precision].
    """
    dt_updated = task.updated_at
    if dt_updated.tzinfo is None:
        dt_updated = dt_updated.replace(tzinfo=timezone.utc)
    else:
        dt_updated = dt_updated.astimezone(timezone.utc)
    canonical_updated_at = dt_updated.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    action_id = f"{action}:{task.id}:{canonical_updated_at}"

    return {
        "action_id": action_id,
        "action": action,
        "entity_type": "task",
        "entity_id": task.id,
        "task_id": task.id,
        "user_id": task.user_id,
        "title": task.title,
        "description": task.description or "",
        "status": task.status.value,
        "priority": task.priority.value,
        "created_at": task.created_at.astimezone(timezone.utc).isoformat(),
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "tags": task.tags,
        "is_active": task.is_active,
        "source_commitment_id": task.source_commitment_id,
        "updated_at": canonical_updated_at,
    }


class CreateTaskTool(BaseTool):
    """
    Tool allowing Aurora to create a new actionable task for the user.
    Risk level: LOW.
    Confirmation: False (constructive action adding a tracked work item).
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="create_task",
            description="Creates a new actionable task for the user. Use this when the user asks to add or remember a task to do.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short actionable title for the task (e.g. 'Review quarterly slide deck')."
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional detailed notes or description."
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Task priority level. Defaults to medium.",
                        "default": "medium"
                    },
                    "due_at": {
                        "type": "string",
                        "description": "Optional ISO 8601 UTC due datetime string (e.g. '2026-10-01T15:00:00Z')."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional categorization tags."
                    },
                    "source_commitment_id": {
                        "type": "string",
                        "description": "Optional linked commitment ID."
                    }
                },
                "required": ["title"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        title = params.get("title", "").strip()
        if not title:
            return {"status": "error", "error": "Parameter 'title' is required and cannot be empty."}

        description = params.get("description", "")
        raw_prio = params.get("priority", "medium")
        try:
            priority = TaskPriority(raw_prio.lower())
        except ValueError:
            priority = TaskPriority.MEDIUM

        due_at = None
        if params.get("due_at"):
            try:
                due_at = datetime.fromisoformat(params["due_at"])
            except ValueError:
                return {"status": "error", "error": f"Invalid ISO datetime format for 'due_at': {params['due_at']}"}

        tags = params.get("tags", [])
        source_commitment_id = params.get("source_commitment_id")

        try:
            task = await self.manager.create_task(
                title=title,
                user_id=user_id,
                description=description,
                priority=priority,
                due_at=due_at,
                tags=tags,
                source_commitment_id=source_commitment_id,
            )

            client_action = build_task_client_action("upsert_task", task)

            return {
                "status": "success",
                "task_id": task.id,
                "title": task.title,
                "priority": task.priority.value,
                "status_value": task.status.value,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "source_commitment_id": task.source_commitment_id,
                "client_action": client_action,
                "message": f"Created task: '{task.title}'"
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class ListTasksTool(BaseTool):
    """
    Tool allowing Aurora to list active or filtered tasks for the current user.
    Risk level: LOW.
    Confirmation: False (read-only query).
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_tasks",
            description="Lists active or filtered tasks for the current user. Use to check what tasks are pending, in progress, or scheduled.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "cancelled"],
                        "description": "Optional filter by task status."
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Optional filter by priority."
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional search query to match title, description, or tags."
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of tasks to return (default 10).",
                        "default": 10
                    }
                }
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")

        statuses = None
        if params.get("status"):
            try:
                statuses = [TaskStatus(params["status"].lower())]
            except ValueError:
                pass

        priorities = None
        if params.get("priority"):
            try:
                priorities = [TaskPriority(params["priority"].lower())]
            except ValueError:
                pass

        query = params.get("query")
        limit = min(50, max(1, params.get("limit", 10)))

        tasks = await self.manager.list_tasks(
            user_id=user_id,
            statuses=statuses,
            priorities=priorities,
            query=query,
            include_inactive=False,
            limit=limit
        )

        formatted = [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "status": t.status.value,
                "priority": t.priority.value,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "tags": t.tags,
                "source_commitment_id": t.source_commitment_id,
            }
            for t in tasks
        ]

        return {
            "status": "success",
            "count": len(formatted),
            "tasks": formatted
        }


class UpdateTaskTool(BaseTool):
    """
    Tool allowing Aurora to update fields on an existing task.
    Risk level: LOW.
    Confirmation: False (constructive update of user work item).
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="update_task",
            description="Updates fields on an existing actionable task (e.g. title, description, priority, due date).",
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique ID of the task to update."
                    },
                    "title": {
                        "type": "string",
                        "description": "Updated short title."
                    },
                    "description": {
                        "type": "string",
                        "description": "Updated detailed notes or description."
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Updated priority level."
                    },
                    "due_at": {
                        "type": "string",
                        "description": "Updated ISO 8601 UTC due datetime string."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Updated categorization tags."
                    },
                    "source_commitment_id": {
                        "type": "string",
                        "description": "Updated linked commitment ID."
                    },
                    "clear_commitment": {
                        "type": "boolean",
                        "description": "If true, clear linked commitment."
                    }
                },
                "required": ["task_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        task_id = params.get("task_id", "").strip()
        if not task_id:
            return {"status": "error", "error": "Parameter 'task_id' is required."}

        title = params.get("title")
        description = params.get("description")
        raw_prio = params.get("priority")
        priority = None
        if raw_prio:
            try:
                priority = TaskPriority(raw_prio.lower())
            except ValueError:
                return {"status": "error", "error": f"Invalid priority: {raw_prio}"}

        due_at = None
        if params.get("due_at"):
            try:
                due_at = datetime.fromisoformat(params["due_at"])
            except ValueError:
                return {"status": "error", "error": f"Invalid ISO datetime format for 'due_at': {params['due_at']}"}

        tags = params.get("tags")
        source_commitment_id = params.get("source_commitment_id")
        clear_commitment = params.get("clear_commitment", False)

        try:
            task, mutated = await self.manager.update_task_with_status(
                task_id=task_id,
                user_id=user_id,
                title=title,
                description=description,
                priority=priority,
                due_at=due_at,
                tags=tags,
                source_commitment_id=source_commitment_id,
                clear_commitment=clear_commitment,
            )
            if not task:
                return {"status": "error", "error": f"Task '{task_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "task_id": task.id,
                "title": task.title,
                "priority": task.priority.value,
                "status_value": task.status.value,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "source_commitment_id": task.source_commitment_id,
                "mutated": mutated,
                "message": f"Updated task '{task.title}'." if mutated else f"Task '{task.title}' already up to date (no-op)."
            }
            if mutated:
                res["client_action"] = build_task_client_action("upsert_task", task)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class StartTaskTool(BaseTool):
    """Mark a pending task in progress; repeated starts are idempotent."""

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="start_task",
            description="Marks a pending task as in progress. Use when the user says they are starting or working on a task.",
            parameters_schema={"type": "object", "properties": {"task_id": {"type": "string", "description": "Unique ID of the task to start."}}, "required": ["task_id"]},
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}
        task_id = str(params.get("task_id", "")).strip()
        if not task_id:
            return {"status": "error", "error": "Parameter 'task_id' is required."}
        try:
            task, mutated = await self.manager.start_task_with_status(task_id, user_id=user_id)
            if task is None:
                return {"status": "error", "error": f"Task '{task_id}' not found."}
            result: Dict[str, Any] = {
                "status": "success", "task_id": task.id, "title": task.title,
                "status_value": task.status.value, "mutated": mutated,
                "message": f"Started task '{task.title}'." if mutated else f"Task '{task.title}' is already in progress (no-op).",
            }
            if mutated:
                result["client_action"] = build_task_client_action("upsert_task", task)
            return result
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class CompleteTaskTool(BaseTool):
    """
    Tool allowing Aurora to mark a task completed.
    Risk level: MEDIUM.
    Confirmation: True (all medium-risk task mutations require explicit approval).
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="complete_task",
            description="Marks an actionable task as completed by task ID.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique ID of the task to mark as completed."
                    }
                },
                "required": ["task_id"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        task_id = params.get("task_id", "").strip()
        if not task_id:
            return {"status": "error", "error": "Parameter 'task_id' is required."}

        try:
            task, mutated = await self.manager.complete_task_with_status(task_id=task_id, user_id=user_id)
            if not task:
                return {"status": "error", "error": f"Task '{task_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "task_id": task.id,
                "title": task.title,
                "status_value": task.status.value,
                "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                "mutated": mutated,
                "message": f"Marked task '{task.title}' as completed." if mutated else f"Task '{task.title}' is already completed (no-op)."
            }
            if mutated:
                res["client_action"] = build_task_client_action("upsert_task", task)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class CancelTaskTool(BaseTool):
    """
    Tool allowing Aurora to cancel a task.
    Risk level: MEDIUM.
    Confirmation: True (destructive lifecycle termination, requires explicit user approval).
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="cancel_task",
            description="Cancels an existing task by ID. Requires user confirmation before cancellation.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique ID of the task to cancel."
                    }
                },
                "required": ["task_id"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.TASKS]
        )

    def prepare_confirmation(self, tool_call: ToolCall) -> PendingConfirmation:
        args = dict(tool_call.arguments or {})
        task_id = args.get("task_id", "").strip()
        desc = f"Cancel task with ID '{task_id}'"

        return PendingConfirmation(
            tool_call=tool_call,
            risk_level=ToolRiskLevel.MEDIUM,
            description=desc
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        task_id = params.get("task_id", "").strip()
        if not task_id:
            return {"status": "error", "error": "Parameter 'task_id' is required."}

        try:
            task, mutated = await self.manager.cancel_task_with_status(task_id=task_id, user_id=user_id)
            if not task:
                return {"status": "error", "error": f"Task '{task_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "task_id": task.id,
                "title": task.title,
                "status_value": task.status.value,
                "mutated": mutated,
                "message": f"Cancelled task '{task.title}'." if mutated else f"Task '{task.title}' is already cancelled (no-op)."
            }
            if mutated:
                res["client_action"] = build_task_client_action("upsert_task", task)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class LinkTaskCommitmentTool(BaseTool):
    """
    Tool allowing Aurora to link a task to a commitment.
    Risk level: LOW.
    Confirmation: False (constructive relationship linking).
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="link_task_commitment",
            description="Links an active task to an active commitment.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique ID of the task to link."
                    },
                    "commitment_id": {
                        "type": "string",
                        "description": "Unique ID of the commitment to link to."
                    }
                },
                "required": ["task_id", "commitment_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        task_id = params.get("task_id", "").strip()
        commitment_id = params.get("commitment_id", "").strip()
        if not task_id or not commitment_id:
            return {"status": "error", "error": "Parameters 'task_id' and 'commitment_id' are required."}

        try:
            task, mutated = await self.manager.link_task_commitment(
                task_id=task_id,
                commitment_id=commitment_id,
                user_id=user_id,
            )
            if not task:
                return {"status": "error", "error": f"Active task '{task_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "task_id": task.id,
                "source_commitment_id": task.source_commitment_id,
                "mutated": mutated,
                "message": f"Linked task '{task.title}' to commitment '{commitment_id}'." if mutated else f"Task '{task.title}' is already linked to commitment '{commitment_id}' (no-op)."
            }
            if mutated:
                res["client_action"] = build_task_client_action("upsert_task", task)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class UnlinkTaskCommitmentTool(BaseTool):
    """
    Tool allowing Aurora to unlink a task from its commitment.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._manager = task_manager

    @property
    def manager(self) -> TaskManager:
        return self._manager or get_task_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="unlink_task_commitment",
            description="Unlinks an active task from its associated commitment.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Unique ID of the task to unlink."
                    }
                },
                "required": ["task_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        task_id = params.get("task_id", "").strip()
        if not task_id:
            return {"status": "error", "error": "Parameter 'task_id' is required."}

        try:
            task, mutated = await self.manager.unlink_task_commitment(
                task_id=task_id,
                user_id=user_id,
            )
            if not task:
                return {"status": "error", "error": f"Active task '{task_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "task_id": task.id,
                "source_commitment_id": task.source_commitment_id,
                "mutated": mutated,
                "message": f"Unlinked task '{task.title}' from commitment." if mutated else f"Task '{task.title}' is already unlinked (no-op)."
            }
            if mutated:
                res["client_action"] = build_task_client_action("upsert_task", task)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
