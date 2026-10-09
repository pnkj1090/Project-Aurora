from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.agenda.horizon import (
    ExecutiveHorizonOrchestrationService,
    get_horizon_service,
)
from app.services.agenda.horizon_models import (
    HorizonProposalType,
    HorizonProposalStatus,
    PreconditionFailedError,
    HorizonStateTransitionError,
)

logger = logging.getLogger("aurora.tools.horizon")


# ==============================================================================
# 1. GetDailyExecutivePlanTool (LOW)
# ==============================================================================

class GetDailyExecutivePlanTool(BaseTool):
    """
    Read-only tool synthesizing today's executive schedule, tasks, commitments,
    free working gaps, and reminders into a coherent morning perspective.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, horizon_service: Optional[ExecutiveHorizonOrchestrationService] = None):
        self._horizon_service = horizon_service

    @property
    def service(self) -> ExecutiveHorizonOrchestrationService:
        return self._horizon_service or get_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_daily_executive_plan",
            description=(
                "Synthesizes today's schedule intervals, open tasks, active commitments, "
                "available focus slots, and scheduled reminders into an executive morning plan."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_date": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD) to plan for. Defaults to today in user timezone."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone (e.g. 'America/New_York'). Defaults to user timezone."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.CALENDAR]  # Evaluated via domain omission policy
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        target_date_raw = params.get("target_date")
        target_date = date.fromisoformat(target_date_raw) if target_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")

        try:
            plan = await self.service.get_daily_executive_plan(
                target_date=target_date,
                explicit_tz=explicit_tz,
                user_id=user_id,
            )
            data = plan.model_dump(mode="json")
            return {
                "status": "success",
                "success": True,
                "data": data,
                "plan": data,
            }
        except PermissionError as exc:
            return {"status": "error", "success": False, "error": "INSUFFICIENT_PERMISSIONS", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("GetDailyExecutivePlanTool error: %s", exc, exc_info=True)
            return {"status": "error", "success": False, "error": "INTERNAL_ERROR", "message": str(exc)}


# ==============================================================================
# 2. ProposeDailyPlanTool (LOW)
# ==============================================================================

class ProposeDailyPlanTool(BaseTool):
    """
    Read-only tool generating a server-held DayPlanProposal from proposed focus allocations and reminders.
    Risk level: LOW.
    Confirmation: False (generates proposal in DRAFT state; mutations execute via apply_daily_plan).
    """

    def __init__(self, horizon_service: Optional[ExecutiveHorizonOrchestrationService] = None):
        self._horizon_service = horizon_service

    @property
    def service(self) -> ExecutiveHorizonOrchestrationService:
        return self._horizon_service or get_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="propose_daily_plan",
            description=(
                "Synthesizes task focus recommendations and milestone reminders into a server-held "
                "immutable DayPlanProposal in DRAFT state awaiting explicit user confirmation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_date": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD)."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone string."
                    },
                    "focus_recommendations": {
                        "type": "array",
                        "description": "List of task-to-slot focus recommendations (presentation-only).",
                        "items": {"type": "object"}
                    },
                    "milestone_reminders": {
                        "type": "array",
                        "description": "List of proposed milestone reminders.",
                        "items": {"type": "object"}
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.REMINDERS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        target_date_raw = params.get("target_date")
        target_date = date.fromisoformat(target_date_raw) if target_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")
        recs = params.get("focus_recommendations")
        rems = params.get("milestone_reminders")

        try:
            proposal = await self.service.propose_daily_plan(
                target_date=target_date,
                explicit_tz=explicit_tz,
                focus_recommendations=recs,
                milestone_reminders=rems,
                user_id=user_id,
            )
            data = proposal.model_dump(mode="json")
            return {
                "status": "success",
                "success": True,
                "data": data,
                "proposal": data,
                "proposal_id": proposal.proposal_id,
                "presentation": proposal.presentation.model_dump(mode="json"),
            }
        except PermissionError as exc:
            return {"status": "error", "success": False, "error": "INSUFFICIENT_PERMISSIONS", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("ProposeDailyPlanTool error: %s", exc, exc_info=True)
            return {"status": "error", "success": False, "error": "INTERNAL_ERROR", "message": str(exc)}


# ==============================================================================
# 3. ApplyDailyPlanTool (HIGH)
# ==============================================================================

class ApplyDailyPlanTool(BaseTool):
    """
    HIGH-risk mutating tool executing a user-approved DayPlanProposal.
    Governed by confirmation path.
    Creates scheduled reminders and emits client notification actions.
    """

    def __init__(self, horizon_service: Optional[ExecutiveHorizonOrchestrationService] = None):
        self._horizon_service = horizon_service

    @property
    def service(self) -> ExecutiveHorizonOrchestrationService:
        return self._horizon_service or get_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_daily_plan",
            description=(
                "Executes a server-held DayPlanProposal following user confirmation. "
                "Creates milestone reminders and schedules local notifications."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Unique ID of the DayPlanProposal to execute."
                    },
                    "approved": {
                        "type": "boolean",
                        "description": "True to execute proposal; False to abort."
                    }
                },
                "required": ["proposal_id"]
            },
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
            required_permissions=[PermissionType.REMINDERS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        proposal_id = params.get("proposal_id")
        approved = params.get("approved", True)

        if not proposal_id:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": "proposal_id is required."}

        try:
            executed = await self.service.execute_proposal(
                proposal_id=proposal_id,
                user_id=user_id,
                approved=approved,
            )
            data = executed.model_dump(mode="json")
            return {
                "status": "success",
                "success": True,
                "data": data,
                "proposal_id": executed.proposal_id,
                "proposal_status": executed.status.value,
                "failure_reason": executed.failure_reason.value if executed.failure_reason else None,
                "executed_proposal": data,
            }
        except PreconditionFailedError as exc:
            return {"status": "error", "success": False, "error": "PRECONDITION_FAILED", "message": str(exc)}
        except HorizonStateTransitionError as exc:
            return {"status": "error", "success": False, "error": "ILLEGAL_STATE_TRANSITION", "message": str(exc)}
        except PermissionError as exc:
            return {"status": "error", "success": False, "error": "INSUFFICIENT_PERMISSIONS", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("ApplyDailyPlanTool error: %s", exc, exc_info=True)
            return {"status": "error", "success": False, "error": "INTERNAL_ERROR", "message": str(exc)}


# ==============================================================================
# 4. GetEveningHorizonReviewTool (LOW)
# ==============================================================================

class GetEveningHorizonReviewTool(BaseTool):
    """
    Read-only tool synthesizing evening perspective on task completions,
    overdue items, rollover recommendations, and reflection candidates.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, horizon_service: Optional[ExecutiveHorizonOrchestrationService] = None):
        self._horizon_service = horizon_service

    @property
    def service(self) -> ExecutiveHorizonOrchestrationService:
        return self._horizon_service or get_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_evening_horizon_review",
            description=(
                "Synthesizes completed vs overdue tasks and commitments across today. "
                "Identifies task rollover candidates and commitment overrun risks."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_date": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD)."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone string."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        target_date_raw = params.get("target_date")
        target_date = date.fromisoformat(target_date_raw) if target_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")

        try:
            review = await self.service.get_evening_horizon_review(
                target_date=target_date,
                explicit_tz=explicit_tz,
                user_id=user_id,
            )
            data = review.model_dump(mode="json")
            return {
                "status": "success",
                "success": True,
                "data": data,
                "review": data,
            }
        except PermissionError as exc:
            return {"status": "error", "success": False, "error": "INSUFFICIENT_PERMISSIONS", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("GetEveningHorizonReviewTool error: %s", exc, exc_info=True)
            return {"status": "error", "success": False, "error": "INTERNAL_ERROR", "message": str(exc)}


# ==============================================================================
# 5. ProposeEveningReviewTool (LOW)
# ==============================================================================

class ProposeEveningReviewTool(BaseTool):
    """
    Read-only tool generating a server-held EveningReviewProposal for rollover tasks,
    next-day reminders, and executive reflection memories.
    Risk level: LOW.
    Confirmation: False (generates proposal in DRAFT state; mutations execute via apply_evening_review).
    """

    def __init__(self, horizon_service: Optional[ExecutiveHorizonOrchestrationService] = None):
        self._horizon_service = horizon_service

    @property
    def service(self) -> ExecutiveHorizonOrchestrationService:
        return self._horizon_service or get_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="propose_evening_review",
            description=(
                "Synthesizes task rollovers, next-day reminders, and reflection notes into a server-held "
                "immutable EveningReviewProposal in DRAFT state awaiting user confirmation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_date": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD)."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone string."
                    },
                    "rollover_task_ids": {
                        "type": "array",
                        "description": "List of task IDs to roll over to tomorrow.",
                        "items": {"type": "string"}
                    },
                    "next_day_reminders": {
                        "type": "array",
                        "description": "List of next-day reminder specifications.",
                        "items": {"type": "object"}
                    },
                    "reflection_content": {
                        "type": "string",
                        "description": "Optional reflection note to store as episodic memory."
                    },
                    "context_tags": {
                        "type": "array",
                        "description": "Optional context tags for the memory.",
                        "items": {"type": "string"}
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        target_date_raw = params.get("target_date")
        target_date = date.fromisoformat(target_date_raw) if target_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")
        rollover_task_ids = params.get("rollover_task_ids")
        rollover_tasks = params.get("rollover_tasks")
        next_day_rems = params.get("next_day_reminders")
        reflection = params.get("reflection_content") or params.get("reflection_notes")
        tags = params.get("context_tags")

        try:
            proposal = await self.service.propose_evening_review(
                target_date=target_date,
                explicit_tz=explicit_tz,
                rollover_task_ids=rollover_task_ids,
                rollover_tasks=rollover_tasks,
                next_day_reminders=next_day_rems,
                reflection_content=reflection,
                context_tags=tags,
                user_id=user_id,
            )
            data = proposal.model_dump(mode="json")
            return {
                "status": "success",
                "success": True,
                "data": data,
                "proposal": data,
                "proposal_id": proposal.proposal_id,
                "presentation": proposal.presentation.model_dump(mode="json"),
            }
        except PermissionError as exc:
            return {"status": "error", "success": False, "error": "INSUFFICIENT_PERMISSIONS", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("ProposeEveningReviewTool error: %s", exc, exc_info=True)
            return {"status": "error", "success": False, "error": "INTERNAL_ERROR", "message": str(exc)}


# ==============================================================================
# 6. ApplyEveningReviewTool (HIGH)
# ==============================================================================

class ApplyEveningReviewTool(BaseTool):
    """
    HIGH-risk mutating tool executing a user-approved EveningReviewProposal.
    Governed by confirmation path.
    Applies task rollovers, creates next-day reminders, and creates reflection memories.
    """

    def __init__(self, horizon_service: Optional[ExecutiveHorizonOrchestrationService] = None):
        self._horizon_service = horizon_service

    @property
    def service(self) -> ExecutiveHorizonOrchestrationService:
        return self._horizon_service or get_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_evening_review",
            description=(
                "Executes a server-held EveningReviewProposal following user confirmation. "
                "Rolls over overdue tasks, creates next-day reminders, and commits reflection memories."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Unique ID of the EveningReviewProposal to execute."
                    },
                    "approved": {
                        "type": "boolean",
                        "description": "True to execute proposal; False to abort."
                    }
                },
                "required": ["proposal_id"]
            },
            risk_level=ToolRiskLevel.HIGH,
            requires_confirmation=True,
            required_permissions=[PermissionType.TASKS]
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        proposal_id = params.get("proposal_id")
        approved = params.get("approved", True)

        if not proposal_id:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": "proposal_id is required."}

        try:
            executed = await self.service.execute_proposal(
                proposal_id=proposal_id,
                user_id=user_id,
                approved=approved,
            )
            data = executed.model_dump(mode="json")
            return {
                "status": "success",
                "success": True,
                "data": data,
                "proposal_id": executed.proposal_id,
                "proposal_status": executed.status.value,
                "failure_reason": executed.failure_reason.value if executed.failure_reason else None,
                "executed_proposal": data,
            }
        except PreconditionFailedError as exc:
            return {"status": "error", "success": False, "error": "PRECONDITION_FAILED", "message": str(exc)}
        except HorizonStateTransitionError as exc:
            return {"status": "error", "success": False, "error": "ILLEGAL_STATE_TRANSITION", "message": str(exc)}
        except PermissionError as exc:
            return {"status": "error", "success": False, "error": "INSUFFICIENT_PERMISSIONS", "message": str(exc)}
        except ValueError as exc:
            return {"status": "error", "success": False, "error": "VALIDATION_ERROR", "message": str(exc)}
        except Exception as exc:
            logger.error("ApplyEveningReviewTool error: %s", exc, exc_info=True)
            return {"status": "error", "success": False, "error": "INTERNAL_ERROR", "message": str(exc)}
