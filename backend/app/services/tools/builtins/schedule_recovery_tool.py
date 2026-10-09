from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.agenda.schedule_recovery import (
    ProposalLifecycleCoordinator,
    get_schedule_recovery_coordinator,
)
from app.services.agenda.schedule_recovery_models import (
    ApplyRecoveryProposalRequest,
    CancelRecoveryProposalRequest,
    EvaluateRecoveryProposalRequest,
    ProposeScheduleRecoveryRequest,
)

logger = logging.getLogger("aurora.tools.schedule_recovery")


# ==============================================================================
# 1. ProposeScheduleRecoveryTool (LOW)
# ==============================================================================

class ProposeScheduleRecoveryTool(BaseTool):
    """
    Constructs an authoritative ScheduleRecoveryProposal across overloaded or drifting tasks and reminders.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, coordinator: Optional[ProposalLifecycleCoordinator] = None):
        self._coordinator = coordinator

    @property
    def coordinator(self) -> ProposalLifecycleCoordinator:
        return self._coordinator or get_schedule_recovery_coordinator()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="propose_schedule_recovery",
            description="Constructs an authoritative ScheduleRecoveryProposal to reallocate conflicting tasks and reminders.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of task IDs to recover/reschedule.",
                    },
                    "reminder_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of reminder IDs to reschedule.",
                    },
                    "target_window_start": {
                        "type": "string",
                        "description": "ISO8601 UTC start of the target recovery window.",
                    },
                    "target_window_end": {
                        "type": "string",
                        "description": "ISO8601 UTC end of the target recovery window.",
                    },
                    "user_intent": {
                        "type": "string",
                        "description": "Optional intent description explaining why recovery is requested.",
                    },
                    "client_preferred_timezone": {
                        "type": "string",
                        "description": "Optional IANA timezone name.",
                    },
                },
                "required": ["task_ids"],
            },
            risk_level=ToolRiskLevel.LOW,
            required_permissions=[PermissionType.TASKS],
            requires_confirmation=False,
        )

    async def execute(self, user_id: str = DEFAULT_LOCAL_USER_ID, **kwargs: Any) -> Dict[str, Any]:
        task_ids = kwargs.get("task_ids") or []
        reminder_ids = kwargs.get("reminder_ids")
        user_intent = kwargs.get("user_intent")
        client_tz = kwargs.get("client_preferred_timezone")

        now_utc = datetime.now(timezone.utc)
        win_start_raw = kwargs.get("target_window_start")
        win_end_raw = kwargs.get("target_window_end")

        if win_start_raw:
            win_start = datetime.fromisoformat(win_start_raw.replace("Z", "+00:00"))
        else:
            win_start = now_utc + timedelta(hours=1)

        if win_end_raw:
            win_end = datetime.fromisoformat(win_end_raw.replace("Z", "+00:00"))
        else:
            win_end = win_start + timedelta(days=2)

        req = ProposeScheduleRecoveryRequest(
            task_ids=task_ids,
            reminder_ids=reminder_ids,
            target_window_start=win_start,
            target_window_end=win_end,
            user_intent=user_intent,
            client_preferred_timezone=client_tz,
        )
        proposal = await self.coordinator.propose_schedule_recovery(user_id=user_id, request=req)
        return proposal.model_dump(mode="json")


# ==============================================================================
# 2. GetScheduleRecoveryProposalTool (LOW)
# ==============================================================================

class GetScheduleRecoveryProposalTool(BaseTool):
    """
    Retrieves snapshot of a ScheduleRecoveryProposal by ID with lazy expiration check.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, coordinator: Optional[ProposalLifecycleCoordinator] = None):
        self._coordinator = coordinator

    @property
    def coordinator(self) -> ProposalLifecycleCoordinator:
        return self._coordinator or get_schedule_recovery_coordinator()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_schedule_recovery_proposal",
            description="Retrieves a stored ScheduleRecoveryProposal snapshot by proposal ID.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Unique identifier of the recovery proposal.",
                    },
                },
                "required": ["proposal_id"],
            },
            risk_level=ToolRiskLevel.LOW,
            required_permissions=[PermissionType.TASKS],
            requires_confirmation=False,
        )

    async def execute(self, user_id: str = DEFAULT_LOCAL_USER_ID, **kwargs: Any) -> Dict[str, Any]:
        proposal_id = kwargs["proposal_id"]
        proposal = await self.coordinator.get_proposal(proposal_id=proposal_id, authenticated_user_id=user_id)
        return proposal.model_dump(mode="json")


# ==============================================================================
# 3. CancelScheduleRecoveryProposalTool (MEDIUM)
# ==============================================================================

class CancelScheduleRecoveryProposalTool(BaseTool):
    """
    Cancels an active ScheduleRecoveryProposal before execution.
    Risk level: MEDIUM.
    Confirmation: False.
    """

    def __init__(self, coordinator: Optional[ProposalLifecycleCoordinator] = None):
        self._coordinator = coordinator

    @property
    def coordinator(self) -> ProposalLifecycleCoordinator:
        return self._coordinator or get_schedule_recovery_coordinator()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="cancel_schedule_recovery_proposal",
            description="Cancels an active pending ScheduleRecoveryProposal.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Unique identifier of the proposal to cancel.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional cancellation reason.",
                    },
                },
                "required": ["proposal_id"],
            },
            risk_level=ToolRiskLevel.MEDIUM,
            required_permissions=[PermissionType.TASKS],
            requires_confirmation=False,
        )

    async def execute(self, user_id: str = DEFAULT_LOCAL_USER_ID, **kwargs: Any) -> Dict[str, Any]:
        proposal_id = kwargs["proposal_id"]
        reason = kwargs.get("reason")
        cancelled = await self.coordinator.cancel_proposal(proposal_id=proposal_id, authenticated_user_id=user_id, reason=reason)
        return cancelled.model_dump(mode="json")


# ==============================================================================
# 4. ApplyScheduleRecoveryProposalTool (HIGH)
# ==============================================================================

class ApplyScheduleRecoveryProposalTool(BaseTool):
    """
    Executes the 24-step mutation pipeline to apply a confirmed ScheduleRecoveryProposal.
    Risk level: HIGH.
    Confirmation: True.
    """

    def __init__(self, coordinator: Optional[ProposalLifecycleCoordinator] = None):
        self._coordinator = coordinator

    @property
    def coordinator(self) -> ProposalLifecycleCoordinator:
        return self._coordinator or get_schedule_recovery_coordinator()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="apply_schedule_recovery_proposal",
            description="Executes a confirmed ScheduleRecoveryProposal through the authoritative 24-step pipeline.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Unique identifier of the proposal to execute.",
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": "Must be set to true to authorize execution.",
                    },
                    "client_context": {
                        "type": "object",
                        "description": "Optional client telemetry context.",
                    },
                },
                "required": ["proposal_id", "confirmed"],
            },
            risk_level=ToolRiskLevel.HIGH,
            required_permissions=[PermissionType.TASKS, PermissionType.REMINDERS],
            requires_confirmation=True,
        )

    async def execute(self, user_id: str = DEFAULT_LOCAL_USER_ID, **kwargs: Any) -> Dict[str, Any]:
        proposal_id = kwargs["proposal_id"]
        confirmed = kwargs.get("confirmed", True)
        client_context = kwargs.get("client_context")

        req = ApplyRecoveryProposalRequest(confirmed=confirmed, client_context=client_context)
        result = await self.coordinator.apply_proposal(proposal_id=proposal_id, authenticated_user_id=user_id, request=req)
        return result.model_dump(mode="json")


# ==============================================================================
# 5. GetScheduleRecoveryResultTool (LOW)
# ==============================================================================

class GetScheduleRecoveryResultTool(BaseTool):
    """
    Retrieves the terminal execution result for a proposal from the ExecutionStore.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, coordinator: Optional[ProposalLifecycleCoordinator] = None):
        self._coordinator = coordinator

    @property
    def coordinator(self) -> ProposalLifecycleCoordinator:
        return self._coordinator or get_schedule_recovery_coordinator()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_schedule_recovery_result",
            description="Retrieves the final execution result and trace of a processed schedule recovery proposal.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Proposal ID whose terminal execution result is requested.",
                    },
                },
                "required": ["proposal_id"],
            },
            risk_level=ToolRiskLevel.LOW,
            required_permissions=[PermissionType.TASKS],
            requires_confirmation=False,
        )

    async def execute(self, user_id: str = DEFAULT_LOCAL_USER_ID, **kwargs: Any) -> Dict[str, Any]:
        proposal_id = kwargs["proposal_id"]
        result = await self.coordinator.get_execution_result(proposal_id=proposal_id, authenticated_user_id=user_id)
        return result.model_dump(mode="json")


# ==============================================================================
# 6. EvaluateScheduleRecoveryTool (LOW)
# ==============================================================================

class EvaluateScheduleRecoveryTool(BaseTool):
    """
    Performs dry-run preflight evaluation of all stored preconditions without mutating state.
    Risk level: LOW.
    Confirmation: False.
    """

    def __init__(self, coordinator: Optional[ProposalLifecycleCoordinator] = None):
        self._coordinator = coordinator

    @property
    def coordinator(self) -> ProposalLifecycleCoordinator:
        return self._coordinator or get_schedule_recovery_coordinator()

    @property
    def risk_level(self) -> ToolRiskLevel:
        return self.definition.risk_level

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="evaluate_schedule_recovery",
            description="Performs read-only validation of all preconditions for a pending schedule recovery proposal.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "proposal_id": {
                        "type": "string",
                        "description": "Proposal ID to dry-run evaluate.",
                    },
                },
                "required": ["proposal_id"],
            },
            risk_level=ToolRiskLevel.LOW,
            required_permissions=[PermissionType.TASKS],
            requires_confirmation=False,
        )

    async def execute(self, user_id: str = DEFAULT_LOCAL_USER_ID, **kwargs: Any) -> Dict[str, Any]:
        proposal_id = kwargs["proposal_id"]
        eval_result = await self.coordinator.evaluate_proposal(proposal_id=proposal_id, authenticated_user_id=user_id)
        return eval_result.model_dump(mode="json")
