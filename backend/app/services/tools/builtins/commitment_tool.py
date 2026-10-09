import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.schemas import ToolCall, PendingConfirmation
from app.services.commitments import (
    get_commitment_manager,
    CommitmentManager,
    CommitmentStatus,
    CommitmentPriority,
    Commitment,
)

logger = logging.getLogger("aurora.tools.commitments")


def build_commitment_client_action(action: str, commitment: Commitment) -> Dict[str, Any]:
    """
    Constructs a deterministic, canonical commitment synchronization client action.
    Action ID format: [action]:[commitment_id]:[canonical UTC updated_at with microsecond precision].
    """
    dt_updated = commitment.updated_at
    if dt_updated.tzinfo is None:
        dt_updated = dt_updated.replace(tzinfo=timezone.utc)
    else:
        dt_updated = dt_updated.astimezone(timezone.utc)
    canonical_updated_at = dt_updated.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    action_id = f"{action}:{commitment.id}:{canonical_updated_at}"

    return {
        "action_id": action_id,
        "action": action,
        "entity_type": "commitment",
        "entity_id": commitment.id,
        "commitment_id": commitment.id,
        "user_id": commitment.user_id,
        "title": commitment.title,
        "description": commitment.description or "",
        "status": commitment.status.value,
        "priority": commitment.priority.value,
        "created_at": commitment.created_at.astimezone(timezone.utc).isoformat(),
        "target_date": commitment.target_date.isoformat() if commitment.target_date else None,
        "completed_at": commitment.completed_at.isoformat() if commitment.completed_at else None,
        "tags": commitment.tags,
        "is_active": commitment.is_active,
        "updated_at": canonical_updated_at,
    }


class CreateCommitmentTool(BaseTool):
    """
    Tool allowing Aurora to create a new tracked commitment or intention.
    Risk level: LOW.
    Confirmation: False (constructive action tracking a user intention).
    """

    def __init__(self, commitment_manager: Optional[CommitmentManager] = None):
        self._manager = commitment_manager

    @property
    def manager(self) -> CommitmentManager:
        return self._manager or get_commitment_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="create_commitment",
            description="Creates a new tracked commitment, agreement, or intention for the user to follow up on.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Commitment title or summary of agreement (e.g. 'Promise to send monthly budget by Friday')."
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional details or context."
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Commitment priority level. Defaults to medium.",
                        "default": "medium"
                    },
                    "target_date": {
                        "type": "string",
                        "description": "Optional ISO 8601 UTC target date string (e.g. '2026-10-15T00:00:00Z')."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional categorization tags."
                    }
                },
                "required": ["title"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.COMMITMENTS]
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
            priority = CommitmentPriority(raw_prio.lower())
        except ValueError:
            priority = CommitmentPriority.MEDIUM

        target_date = None
        if params.get("target_date"):
            try:
                target_date = datetime.fromisoformat(params["target_date"])
            except ValueError:
                return {"status": "error", "error": f"Invalid ISO datetime format for 'target_date': {params['target_date']}"}

        tags = params.get("tags", [])

        try:
            commitment = await self.manager.create_commitment(
                title=title,
                user_id=user_id,
                description=description,
                priority=priority,
                target_date=target_date,
                tags=tags
            )

            client_action = build_commitment_client_action("upsert_commitment", commitment)

            return {
                "status": "success",
                "commitment_id": commitment.id,
                "title": commitment.title,
                "priority": commitment.priority.value,
                "status_value": commitment.status.value,
                "target_date": commitment.target_date.isoformat() if commitment.target_date else None,
                "client_action": client_action,
                "message": f"Created commitment: '{commitment.title}'"
            }
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class ListCommitmentsTool(BaseTool):
    """
    Tool allowing Aurora to list active or filtered commitments for the current user.
    Risk level: LOW.
    Confirmation: False (read-only query).
    """

    def __init__(self, commitment_manager: Optional[CommitmentManager] = None):
        self._manager = commitment_manager

    @property
    def manager(self) -> CommitmentManager:
        return self._manager or get_commitment_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_commitments",
            description="Lists tracked commitments or intentions for the current user.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "completed", "cancelled"],
                        "description": "Optional filter by commitment status."
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
                        "description": "Maximum number of commitments to return (default 10).",
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
                statuses = [CommitmentStatus(params["status"].lower())]
            except ValueError:
                pass

        priorities = None
        if params.get("priority"):
            try:
                priorities = [CommitmentPriority(params["priority"].lower())]
            except ValueError:
                pass

        query = params.get("query")
        limit = min(50, max(1, params.get("limit", 10)))

        commitments = await self.manager.list_commitments(
            user_id=user_id,
            statuses=statuses,
            priorities=priorities,
            query=query,
            include_inactive=False,
            limit=limit
        )

        formatted = [
            {
                "id": c.id,
                "title": c.title,
                "description": c.description,
                "status": c.status.value,
                "priority": c.priority.value,
                "target_date": c.target_date.isoformat() if c.target_date else None,
                "tags": c.tags
            }
            for c in commitments
        ]

        return {
            "status": "success",
            "count": len(formatted),
            "commitments": formatted
        }


class UpdateCommitmentTool(BaseTool):
    """
    Tool allowing Aurora to update fields on an existing commitment.
    Risk level: LOW.
    Confirmation: False (constructive update of user commitment).
    """

    def __init__(self, commitment_manager: Optional[CommitmentManager] = None):
        self._manager = commitment_manager

    @property
    def manager(self) -> CommitmentManager:
        return self._manager or get_commitment_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="update_commitment",
            description="Updates fields on an existing commitment (e.g. title, description, priority, target date).",
            parameters_schema={
                "type": "object",
                "properties": {
                    "commitment_id": {
                        "type": "string",
                        "description": "Unique ID of the commitment to update."
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
                    "target_date": {
                        "type": "string",
                        "description": "Updated ISO 8601 UTC target date string."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Updated categorization tags."
                    }
                },
                "required": ["commitment_id"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.COMMITMENTS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        commitment_id = params.get("commitment_id", "").strip()
        if not commitment_id:
            return {"status": "error", "error": "Parameter 'commitment_id' is required."}

        title = params.get("title")
        description = params.get("description")
        raw_prio = params.get("priority")
        priority = None
        if raw_prio:
            try:
                priority = CommitmentPriority(raw_prio.lower())
            except ValueError:
                return {"status": "error", "error": f"Invalid priority: {raw_prio}"}

        target_date = None
        if params.get("target_date"):
            try:
                target_date = datetime.fromisoformat(params["target_date"])
            except ValueError:
                return {"status": "error", "error": f"Invalid ISO datetime format for 'target_date': {params['target_date']}"}

        tags = params.get("tags")

        try:
            commitment, mutated = await self.manager.update_commitment_with_status(
                commitment_id=commitment_id,
                user_id=user_id,
                title=title,
                description=description,
                priority=priority,
                target_date=target_date,
                tags=tags,
            )
            if not commitment:
                return {"status": "error", "error": f"Commitment '{commitment_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "commitment_id": commitment.id,
                "title": commitment.title,
                "priority": commitment.priority.value,
                "status_value": commitment.status.value,
                "target_date": commitment.target_date.isoformat() if commitment.target_date else None,
                "mutated": mutated,
                "message": f"Updated commitment '{commitment.title}'." if mutated else f"Commitment '{commitment.title}' already up to date (no-op)."
            }
            if mutated:
                res["client_action"] = build_commitment_client_action("upsert_commitment", commitment)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class CompleteCommitmentTool(BaseTool):
    """
    Tool allowing Aurora to mark a commitment completed.
    Risk level: MEDIUM.
    Confirmation: True (all medium-risk commitment mutations require explicit approval).
    """

    def __init__(self, commitment_manager: Optional[CommitmentManager] = None):
        self._manager = commitment_manager

    @property
    def manager(self) -> CommitmentManager:
        return self._manager or get_commitment_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="complete_commitment",
            description="Marks a tracked commitment or intention as completed by ID.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "commitment_id": {
                        "type": "string",
                        "description": "Unique ID of the commitment to mark as completed."
                    }
                },
                "required": ["commitment_id"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.COMMITMENTS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        commitment_id = params.get("commitment_id", "").strip()
        if not commitment_id:
            return {"status": "error", "error": "Parameter 'commitment_id' is required."}

        try:
            commitment, mutated = await self.manager.complete_commitment_with_status(commitment_id=commitment_id, user_id=user_id)
            if not commitment:
                return {"status": "error", "error": f"Commitment '{commitment_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "commitment_id": commitment.id,
                "title": commitment.title,
                "status_value": commitment.status.value,
                "completed_at": commitment.completed_at.isoformat() if commitment.completed_at else None,
                "mutated": mutated,
                "message": f"Marked commitment '{commitment.title}' as completed." if mutated else f"Commitment '{commitment.title}' is already completed (no-op)."
            }
            if mutated:
                res["client_action"] = build_commitment_client_action("upsert_commitment", commitment)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}


class CancelCommitmentTool(BaseTool):
    """
    Tool allowing Aurora to cancel a commitment.
    Risk level: MEDIUM.
    Confirmation: True (destructive termination of intention, requires explicit user approval).
    """

    def __init__(self, commitment_manager: Optional[CommitmentManager] = None):
        self._manager = commitment_manager

    @property
    def manager(self) -> CommitmentManager:
        return self._manager or get_commitment_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="cancel_commitment",
            description="Cancels an existing commitment by ID. Requires user confirmation before cancellation.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "commitment_id": {
                        "type": "string",
                        "description": "Unique ID of the commitment to cancel."
                    }
                },
                "required": ["commitment_id"]
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=[PermissionType.COMMITMENTS]
        )

    def prepare_confirmation(self, tool_call: ToolCall) -> PendingConfirmation:
        args = dict(tool_call.arguments or {})
        commitment_id = args.get("commitment_id", "").strip()
        desc = f"Cancel commitment with ID '{commitment_id}'"

        return PendingConfirmation(
            tool_call=tool_call,
            risk_level=ToolRiskLevel.MEDIUM,
            description=desc
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id")
        if not user_id or not str(user_id).strip():
            return {"status": "error", "error": "Server-resolved user identity is required."}

        commitment_id = params.get("commitment_id", "").strip()
        if not commitment_id:
            return {"status": "error", "error": "Parameter 'commitment_id' is required."}

        try:
            commitment, mutated = await self.manager.cancel_commitment_with_status(commitment_id=commitment_id, user_id=user_id)
            if not commitment:
                return {"status": "error", "error": f"Commitment '{commitment_id}' not found."}

            res: Dict[str, Any] = {
                "status": "success",
                "commitment_id": commitment.id,
                "title": commitment.title,
                "status_value": commitment.status.value,
                "mutated": mutated,
                "message": f"Cancelled commitment '{commitment.title}'." if mutated else f"Commitment '{commitment.title}' is already cancelled (no-op)."
            }
            if mutated:
                res["client_action"] = build_commitment_client_action("upsert_commitment", commitment)

            return res
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
