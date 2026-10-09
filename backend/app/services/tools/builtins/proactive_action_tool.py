"""
Phase 5I — AcceptProactiveCandidateTool.

Allows Aurora to accept an active, eligible proactive candidate proposal on behalf of the user.
Initiates execution resolution via ProactiveActionExecutor.

CRITICAL SAFETY RULES:
- Strictly NO 'confirmed', 'approved', 'is_pre_approved', or 'action_payload' arguments.
- The LLM cannot bypass Phase 3 confirmation.
- If underlying action requires confirmation, a PendingConfirmation is returned.
"""
from typing import TYPE_CHECKING, Dict, Any, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel

if TYPE_CHECKING:
    from app.services.proactive.executor import ProactiveActionExecutor


class AcceptProactiveCandidateTool(BaseTool):
    """
    Tool allowing Aurora to accept an eligible proactive candidate proposal.
    Risk level: LOW (constructive proposal acceptance; confirmation-required underlying actions
    will still produce a PendingConfirmation).
    """

    def __init__(self, executor: Optional[Any] = None):
        self._executor = executor

    @property
    def executor(self) -> Any:
        if self._executor is not None:
            return self._executor
        from app.services.proactive.executor import get_proactive_action_executor
        return get_proactive_action_executor()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="accept_proactive_candidate",
            description=(
                "Accepts an active, eligible proactive candidate proposal. "
                "Initiates safe execution resolution. If confirmation is required, "
                "the action will pause awaiting user confirmation."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "candidate_id": {
                        "type": "string",
                        "description": "Unique ID of the proactive candidate proposal to accept.",
                    },
                    "conversation_id": {
                        "type": "string",
                        "description": "Optional active conversation ID to attach any pending confirmation.",
                    },
                },
                "required": ["candidate_id"],
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        user_id = params.get("_user_id") or params.get("user_id") or DEFAULT_LOCAL_USER_ID
        candidate_id = params.get("candidate_id", "").strip()
        if not candidate_id:
            return {"status": "error", "error": "Parameter 'candidate_id' is required."}

        conversation_id = params.get("conversation_id")

        response = await self.executor.resolve_candidate_action(
            candidate_id=candidate_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        res_dict = response.model_dump()
        return res_dict
