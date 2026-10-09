import logging
from typing import Dict, Any, Optional

from app.services.tools.base import ToolRiskLevel
from app.services.tools.schemas import ToolCall, ToolResult, PendingConfirmation
from app.services.tools.registry import ToolRegistry, default_tool_registry
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.identity.models import DEFAULT_LOCAL_USER_ID

logger = logging.getLogger("aurora.tools.executor")


class ToolExecutionError(Exception):
    """Raised when an unrecoverable tool execution error occurs."""
    pass


class ToolConfirmationRequired(Exception):
    """Raised when a tool call cannot proceed without explicit user confirmation."""
    def __init__(self, pending: PendingConfirmation):
        self.pending = pending
        super().__init__(f"Tool '{pending.tool_call.name}' requires user confirmation.")


class ToolExecutor:
    """
    Dedicated executor validating parameters, checking risk and permissions,
    enforcing confirmation requirements, and executing registered tools safely.
    """

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        permission_manager: Optional[PermissionManager] = None,
    ):
        self.registry = registry or default_tool_registry
        self.permission_manager = permission_manager or default_permission_manager

    async def check_permission_requirement(
        self,
        tool_call: ToolCall,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Optional[str]:
        """
        Inspects whether a requested tool call satisfies its required permissions.
        Returns an error message string if any required permission is not AUTHORIZED,
        or None if all permissions are authorized or none are required.
        """
        tool = self.registry.get(tool_call.name)
        if not tool:
            return None

        for perm in tool.definition.required_permissions:
            is_auth = await self.permission_manager.is_authorized(perm, user_id=user_id)
            if not is_auth:
                status = await self.permission_manager.get_status(perm, user_id=user_id)
                perm_str = perm.value if hasattr(perm, "value") else str(perm)
                return f"Action blocked: Required permission '{perm_str}' is not authorized (status: '{status.value}')."
        return None

    def check_confirmation_requirement(self, tool_call: ToolCall) -> Optional[PendingConfirmation]:
        """
        Inspects whether a requested tool call requires confirmation.
        Returns PendingConfirmation if confirmation is required, else None.
        """
        tool = self.registry.get(tool_call.name)
        if not tool:
            return None

        # Tools with requires_confirmation=True or HIGH risk level require user consent
        if tool.definition.requires_confirmation or tool.definition.risk_level == ToolRiskLevel.HIGH:
            if hasattr(tool, "prepare_confirmation"):
                try:
                    custom_pending = tool.prepare_confirmation(tool_call)
                    if custom_pending is not None:
                        return custom_pending
                except Exception as exc:
                    logger.warning("Error in tool.prepare_confirmation for '%s': %s", tool_call.name, exc)

            return PendingConfirmation(
                tool_call=tool_call,
                risk_level=tool.definition.risk_level,
                description=tool.definition.description,
            )
        return None

    def validate_arguments(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """
        Validates arguments against the tool's parameter schema.
        Returns an error message string if invalid, None if valid.
        """
        tool = self.registry.get(tool_name)
        if not tool:
            return f"Tool '{tool_name}' is not registered."

        schema = tool.definition.parameters_schema
        required_fields = schema.get("required", [])

        for req in required_fields:
            if req not in args or args[req] is None or (isinstance(args[req], str) and not args[req].strip()):
                return f"Missing required parameter '{req}' for tool '{tool_name}'."

        return None

    async def execute(
        self,
        tool_call: ToolCall,
        is_pre_approved: bool = False,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> ToolResult:
        """
        Executes a validated tool call.
        Enforces declarative permission checks, confirmation requirements, and argument validation.
        """
        tool = self.registry.get(tool_call.name)
        if not tool:
            logger.warning("Attempted execution of unknown tool: %s", tool_call.name)
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                output={"error": f"Tool '{tool_call.name}' not found."},
                success=False,
                error=f"Tool '{tool_call.name}' is not registered."
            )

        # 1. Enforce permission checks first
        perm_error = await self.check_permission_requirement(tool_call, user_id=user_id)
        if perm_error:
            logger.warning("Tool '%s' blocked by permission check: %s", tool_call.name, perm_error)
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                output={"error": perm_error},
                success=False,
                error=perm_error
            )

        # 2. Enforce confirmation check
        if not is_pre_approved:
            pending = self.check_confirmation_requirement(tool_call)
            if pending:
                raise ToolConfirmationRequired(pending)

        # 2. Validate arguments
        val_error = self.validate_arguments(tool_call.name, tool_call.arguments)
        if val_error:
            logger.warning("Tool argument validation failed for %s: %s", tool_call.name, val_error)
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                output={"error": val_error},
                success=False,
                error=val_error
            )

        # 3. Execute tool safely
        try:
            logger.info("Executing tool '%s' (call_id: %s)", tool_call.name, tool_call.id)
            exec_params = dict(tool_call.arguments or {})
            if user_id:
                exec_params["_user_id"] = user_id
            elif "_user_id" in exec_params:
                del exec_params["_user_id"]
            result_data = await tool.execute(exec_params)
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                output=result_data,
                success=True
            )
        except Exception as exc:
            logger.exception("Unexpected error executing tool '%s': %s", tool_call.name, str(exc))
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                output={"error": f"Execution error: {str(exc)}"},
                success=False,
                error=str(exc)
            )
