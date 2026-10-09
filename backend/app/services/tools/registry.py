import logging
from typing import Dict, List, Optional

from app.services.tools.base import BaseTool, ToolDefinition
from app.services.tools.builtins.time_tool import GetCurrentTimeTool
from app.services.tools.builtins.calculator_tool import CalculateTool
from app.services.tools.builtins.reminder_tool import (
    CreateTestReminderTool,
    CreateReminderTool,
    ListRemindersTool,
    UpdateReminderTool,
    SnoozeReminderTool,
    CancelReminderTool,
    LinkReminderToItemTool,
    UnlinkReminderFromItemTool,
)
from app.services.tools.builtins.memory_tool import (
    StoreUserMemoryTool,
    RecallUserMemoryTool,
    ForgetUserMemoryTool,
)
from app.services.tools.builtins.task_tool import (
    CreateTaskTool,
    ListTasksTool,
    UpdateTaskTool,
    StartTaskTool,
    CompleteTaskTool,
    CancelTaskTool,
    LinkTaskCommitmentTool,
    UnlinkTaskCommitmentTool,
)
from app.services.tools.builtins.commitment_tool import (
    CreateCommitmentTool,
    ListCommitmentsTool,
    UpdateCommitmentTool,
    CompleteCommitmentTool,
    CancelCommitmentTool,
)
from app.services.tools.builtins.location_tool import GetCurrentLocationTool
from app.services.tools.builtins.calendar_tool import (
    GetUpcomingCalendarEventsTool,
    AnalyzeScheduleAvailabilityTool,
)
from app.services.tools.builtins.contacts_tool import GetContactsTool
from app.services.tools.builtins.assets_tool import GetSelectedAssetsTool
from app.services.tools.builtins.proactive_tool import (
    ListProactiveCandidatesTool,
    DismissProactiveCandidateTool,
)
from app.services.tools.builtins.proactive_action_tool import AcceptProactiveCandidateTool
from app.services.tools.builtins.agenda_tool import (
    GetDailyAgendaTool,
    ProposeScheduleSlotTool,
    DetectScheduleConflictsTool,
)
from app.services.tools.builtins.coordination_tool import (
    CoordinateScheduleTool,
    ResolveConflictsTool,
    ApplyCoordinationProposalTool,
)
from app.services.tools.builtins.briefing_tool import (
    GenerateEventBriefingTool,
    ProposeEventDebriefTool,
    ApplyEventDebriefTool,
)
from app.services.tools.builtins.horizon_tool import (
    GetDailyExecutivePlanTool,
    ProposeDailyPlanTool,
    ApplyDailyPlanTool,
    GetEveningHorizonReviewTool,
    ProposeEveningReviewTool,
    ApplyEveningReviewTool,
)
from app.services.tools.builtins.weekly_horizon_tool import (
    GetWeeklyStrategicHorizonTool,
    ProposeWeeklyPlanTool,
    ApplyWeeklyPlanTool,
    GetWeeklyRetrospectiveTool,
    ProposeWeeklyRetrospectiveTool,
    ApplyWeeklyRetrospectiveTool,
)
from app.services.tools.builtins.schedule_recovery_tool import (
    ProposeScheduleRecoveryTool,
    GetScheduleRecoveryProposalTool,
    CancelScheduleRecoveryProposalTool,
    ApplyScheduleRecoveryProposalTool,
    GetScheduleRecoveryResultTool,
    EvaluateScheduleRecoveryTool,
)
from app.services.tools.builtins.schedule_disruption_tool import (
    ScheduleDisruptionDiagnosticTool,
)


logger = logging.getLogger("aurora.tools.registry")




class ToolRegistry:
    """
    Central registry for discovering, registering, and managing assistant tools.
    Future tools (Calendar, Files, Web, Maps) are added here without altering core logic.
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        name = tool.definition.name
        if name in self._tools:
            logger.info("Overwriting existing tool registration: %s", name)
        self._tools[name] = tool
        logger.debug("Registered tool: %s", name)

    def unregister(self, name: str) -> bool:
        """Unregister a tool by name."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a registered tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        """List all currently registered tool instances."""
        return list(self._tools.values())

    def get_definitions(self) -> List[ToolDefinition]:
        """Return provider-agnostic definitions for all registered tools."""
        return [tool.definition for tool in self._tools.values()]


def create_default_tool_registry(include_web_tools: bool = False) -> ToolRegistry:
    """Factory creating a registry pre-populated with default built-in tools."""
    registry = ToolRegistry()
    registry.register(GetCurrentTimeTool())
    registry.register(CalculateTool())
    registry.register(CreateTestReminderTool())
    registry.register(CreateReminderTool())
    registry.register(ListRemindersTool())
    registry.register(UpdateReminderTool())
    registry.register(SnoozeReminderTool())
    registry.register(CancelReminderTool())
    registry.register(LinkReminderToItemTool())
    registry.register(UnlinkReminderFromItemTool())
    registry.register(StoreUserMemoryTool())
    registry.register(RecallUserMemoryTool())
    registry.register(ForgetUserMemoryTool())
    registry.register(CreateTaskTool())
    registry.register(ListTasksTool())
    registry.register(UpdateTaskTool())
    registry.register(StartTaskTool())
    registry.register(CompleteTaskTool())
    registry.register(CancelTaskTool())
    registry.register(LinkTaskCommitmentTool())
    registry.register(UnlinkTaskCommitmentTool())
    registry.register(CreateCommitmentTool())
    registry.register(ListCommitmentsTool())
    registry.register(UpdateCommitmentTool())
    registry.register(CompleteCommitmentTool())
    registry.register(CancelCommitmentTool())
    registry.register(GetCurrentLocationTool())
    registry.register(GetUpcomingCalendarEventsTool())
    registry.register(AnalyzeScheduleAvailabilityTool())
    registry.register(GetContactsTool())
    registry.register(GetSelectedAssetsTool())
    registry.register(ListProactiveCandidatesTool())
    registry.register(DismissProactiveCandidateTool())
    registry.register(AcceptProactiveCandidateTool())
    registry.register(GetDailyAgendaTool())
    registry.register(ProposeScheduleSlotTool())
    registry.register(DetectScheduleConflictsTool())
    registry.register(CoordinateScheduleTool())
    registry.register(ResolveConflictsTool())
    registry.register(ApplyCoordinationProposalTool())
    registry.register(GenerateEventBriefingTool())
    registry.register(ProposeEventDebriefTool())
    registry.register(ApplyEventDebriefTool())
    registry.register(GetDailyExecutivePlanTool())
    registry.register(ProposeDailyPlanTool())
    registry.register(ApplyDailyPlanTool())
    registry.register(GetEveningHorizonReviewTool())
    registry.register(ProposeEveningReviewTool())
    registry.register(ApplyEveningReviewTool())
    registry.register(GetWeeklyStrategicHorizonTool())
    registry.register(ProposeWeeklyPlanTool())
    registry.register(ApplyWeeklyPlanTool())
    registry.register(GetWeeklyRetrospectiveTool())
    registry.register(ProposeWeeklyRetrospectiveTool())
    registry.register(ApplyWeeklyRetrospectiveTool())
    registry.register(ProposeScheduleRecoveryTool())
    registry.register(GetScheduleRecoveryProposalTool())
    registry.register(CancelScheduleRecoveryProposalTool())
    registry.register(ApplyScheduleRecoveryProposalTool())
    registry.register(GetScheduleRecoveryResultTool())
    registry.register(EvaluateScheduleRecoveryTool())
    registry.register(ScheduleDisruptionDiagnosticTool())


    if include_web_tools:

        from app.services.web.tools import register_web_tools
        register_web_tools(registry)

    return registry


def register_web_tools(registry: ToolRegistry) -> None:
    """Convenience export to register web tools onto any ToolRegistry."""
    from app.services.web.tools import register_web_tools as _register
    _register(registry)


# Singleton default registry for application runtime
default_tool_registry = create_default_tool_registry()
