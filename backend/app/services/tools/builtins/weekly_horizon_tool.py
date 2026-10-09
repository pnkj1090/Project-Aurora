from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.agenda.weekly_horizon import (
    ExecutiveWeeklyHorizonService,
    get_weekly_horizon_service,
)
from app.services.agenda.weekly_horizon_models import (
    WeeklyProposalType,
    WeeklyProposalStatus,
    PreconditionFailedError,
    WeeklyStateTransitionError,
)

logger = logging.getLogger("aurora.tools.weekly_horizon")


# ==============================================================================
# 1. GetWeeklyStrategicHorizonTool (LOW)
# ==============================================================================

class GetWeeklyStrategicHorizonTool(BaseTool):
    """
    Read-only tool synthesizing the 7-day rolling horizon schedule density,
    daily focus capacities, commitment health, and strategic recommendations.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, weekly_service: Optional[ExecutiveWeeklyHorizonService] = None):
        self._weekly_service = weekly_service

    @property
    def service(self) -> ExecutiveWeeklyHorizonService:
        return self._weekly_service or get_weekly_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_weekly_strategic_horizon",
            description=(
                "Synthesizes an upcoming 7-day executive horizon projection across Calendar, "
                "Tasks, and Commitments, analyzing daily focus capacity, congestion, and commitment health."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "target_start_date": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD) to start the 7-day rolling window. Defaults to today."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier (e.g. 'America/New_York')."
                    },
                    "work_start_hour": {
                        "type": "integer",
                        "description": "Optional working hours start (0-23). Defaults to 9."
                    },
                    "work_end_hour": {
                        "type": "integer",
                        "description": "Optional working hours end (1-24). Defaults to 18."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.CALENDAR, PermissionType.TASKS],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        target_start_date_raw = params.get("target_start_date")
        target_start_date = date.fromisoformat(target_start_date_raw) if target_start_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")
        work_start = params.get("work_start_hour")
        work_end = params.get("work_end_hour")

        horizon = await self.service.get_weekly_strategic_horizon(
            target_start_date=target_start_date,
            explicit_tz=explicit_tz,
            work_start_hour=work_start,
            work_end_hour=work_end,
            user_id=user_id,
        )
        return horizon.model_dump()


# ==============================================================================
# 2. ProposeWeeklyPlanTool (LOW)
# ==============================================================================

class ProposeWeeklyPlanTool(BaseTool):
    """
    Creates an immutable, server-held WeeklyPlanProposal in DRAFT status.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, weekly_service: Optional[ExecutiveWeeklyHorizonService] = None):
        self._weekly_service = weekly_service

    @property
    def service(self) -> ExecutiveWeeklyHorizonService:
        return self._weekly_service or get_weekly_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="propose_weekly_plan",
            description=(
                "Synthesizes multi-day task due date allocations and milestone reminders into a "
                "formal, server-held WeeklyPlanProposal in DRAFT status."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_allocations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "task_id": {"type": "string"},
                                "target_date": {"type": "string"}
                            },
                            "required": ["task_id", "target_date"]
                        },
                        "description": "List of task allocations to schedule on verified free slots."
                    },
                    "milestone_reminders": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "scheduled_at": {"type": "string"},
                                "linked_entity_type": {"type": "string", "enum": ["none", "task", "commitment"]},
                                "linked_entity_id": {"type": "string"}
                            },
                            "required": ["title", "scheduled_at"]
                        },
                        "description": "List of milestone reminders to create."
                    },
                    "strategic_intent_notes": {
                        "type": "string",
                        "description": "Optional explicit notes from user to persist as episodic strategic memory."
                    },
                    "target_start_date": {
                        "type": "string",
                        "description": "Optional ISO start date (YYYY-MM-DD)."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier."
                    },
                    "work_start_hour": {"type": "integer"},
                    "work_end_hour": {"type": "integer"}
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        task_allocations = params.get("task_allocations")
        milestone_reminders = params.get("milestone_reminders")
        strategic_intent_notes = params.get("strategic_intent_notes")
        target_start_date_raw = params.get("target_start_date")
        target_start_date = date.fromisoformat(target_start_date_raw) if target_start_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")
        work_start = params.get("work_start_hour")
        work_end = params.get("work_end_hour")

        proposal = await self.service.propose_weekly_plan(
            task_allocations=task_allocations,
            milestone_reminders=milestone_reminders,
            strategic_intent_notes=strategic_intent_notes,
            target_start_date=target_start_date,
            explicit_tz=explicit_tz,
            work_start_hour=work_start,
            work_end_hour=work_end,
            user_id=user_id,
        )
        return proposal.model_dump()


# ==============================================================================
# 3. ApplyWeeklyPlanTool (HIGH)
# ==============================================================================

class ApplyWeeklyPlanTool(BaseTool):
    """
    Executes a confirmed WeeklyPlanProposal.
    Risk level: HIGH.
    Confirmation: True.
    """

    def __init__(self, weekly_service: Optional[ExecutiveWeeklyHorizonService] = None):
        self._weekly_service = weekly_service

    @property
    def service(self) -> ExecutiveWeeklyHorizonService:
        return self._weekly_service or get_weekly_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_weekly_plan",
            description=(
                "Executes a confirmed WeeklyPlanProposal: updates task due dates to verified free slots, "
                "creates milestone reminders, and commits strategic memory."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "The unique proposal identifier (e.g. wpr_...)."
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
            required_permissions=[],  # Revalidated dynamically per operation
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        proposal_id = params.get("proposal_id")
        if not proposal_id:
            raise ValueError("proposal_id is required.")
        approved = params.get("approved", True)

        proposal = await self.service.apply_weekly_plan(
            proposal_id=proposal_id,
            approved=approved,
            user_id=user_id,
        )
        return proposal.model_dump()


# ==============================================================================
# 4. GetWeeklyRetrospectiveTool (LOW)
# ==============================================================================

class GetWeeklyRetrospectiveTool(BaseTool):
    """
    Read-only tool synthesizing the past 7 days: completed tasks, fulfilled commitments,
    unfulfilled commitments, recurring rollovers, and evening reflection memories.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, weekly_service: Optional[ExecutiveWeeklyHorizonService] = None):
        self._weekly_service = weekly_service

    @property
    def service(self) -> ExecutiveWeeklyHorizonService:
        return self._weekly_service or get_weekly_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_weekly_retrospective",
            description=(
                "Synthesizes an executive retrospective lookback over the past 7 days across "
                "completed tasks, honored commitments, unfulfilled deadlines, and reflection memories."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "lookback_end_date": {
                        "type": "string",
                        "description": "Optional ISO date (YYYY-MM-DD) for end of 7-day lookback. Defaults to today."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS, PermissionType.COMMITMENTS],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        lookback_end_date_raw = params.get("lookback_end_date")
        lookback_end_date = date.fromisoformat(lookback_end_date_raw) if lookback_end_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")

        retrospective = await self.service.get_weekly_retrospective(
            lookback_end_date=lookback_end_date,
            explicit_tz=explicit_tz,
            user_id=user_id,
        )
        return retrospective.model_dump()


# ==============================================================================
# 5. ProposeWeeklyRetrospectiveTool (LOW)
# ==============================================================================

class ProposeWeeklyRetrospectiveTool(BaseTool):
    """
    Creates a server-held WeeklyRetrospectiveProposal in DRAFT status.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, weekly_service: Optional[ExecutiveWeeklyHorizonService] = None):
        self._weekly_service = weekly_service

    @property
    def service(self) -> ExecutiveWeeklyHorizonService:
        return self._weekly_service or get_weekly_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="propose_weekly_retrospective",
            description=(
                "Proposes completion of factually honored commitments and creation of an executive "
                "weekly retrospective memory in a server-held proposal."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "commitment_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of commitment IDs proposed for completion."
                    },
                    "retrospective_notes": {
                        "type": "string",
                        "description": "Optional user-supplied strategic retrospective notes to commit to memory."
                    },
                    "lookback_end_date": {
                        "type": "string",
                        "description": "Optional ISO end date (YYYY-MM-DD)."
                    },
                    "explicit_tz": {
                        "type": "string",
                        "description": "Optional IANA timezone identifier."
                    }
                },
                "required": []
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[PermissionType.TASKS, PermissionType.COMMITMENTS],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        commitment_ids = params.get("commitment_ids")
        retrospective_notes = params.get("retrospective_notes")
        lookback_end_date_raw = params.get("lookback_end_date")
        lookback_end_date = date.fromisoformat(lookback_end_date_raw) if lookback_end_date_raw else None
        explicit_tz = params.get("explicit_tz") or params.get("explicit_timezone")

        proposal = await self.service.propose_weekly_retrospective(
            commitment_ids=commitment_ids,
            retrospective_notes=retrospective_notes,
            lookback_end_date=lookback_end_date,
            explicit_tz=explicit_tz,
            user_id=user_id,
        )
        return proposal.model_dump()


# ==============================================================================
# 6. ApplyWeeklyRetrospectiveTool (HIGH)
# ==============================================================================

class ApplyWeeklyRetrospectiveTool(BaseTool):
    """
    Executes a confirmed WeeklyRetrospectiveProposal.
    Risk level: HIGH.
    Confirmation: True.
    """

    def __init__(self, weekly_service: Optional[ExecutiveWeeklyHorizonService] = None):
        self._weekly_service = weekly_service

    @property
    def service(self) -> ExecutiveWeeklyHorizonService:
        return self._weekly_service or get_weekly_horizon_service()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_weekly_retrospective",
            description=(
                "Executes a confirmed WeeklyRetrospectiveProposal: completes honored commitments "
                "and commits weekly retrospective memory."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "The unique proposal identifier (e.g. wpr_...)."
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
            required_permissions=[PermissionType.COMMITMENTS],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = params or {}
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        proposal_id = params.get("proposal_id")
        if not proposal_id:
            raise ValueError("proposal_id is required.")
        approved = params.get("approved", True)

        proposal = await self.service.apply_weekly_retrospective(
            proposal_id=proposal_id,
            approved=approved,
            user_id=user_id,
        )
        return proposal.model_dump()
