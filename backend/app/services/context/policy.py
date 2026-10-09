from typing import Optional, Tuple, Union
import logging

from app.services.context.base import ContextBlock, BaseContextProvider, ContextSensitivity
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.identity.models import DEFAULT_LOCAL_USER_ID

logger = logging.getLogger("aurora.context.policy")


class ContextAccessPolicy:
    """
    Lightweight, deterministic access policy governing whether situational context
    is permitted to be assembled and injected into the prompt.

    Enforces the strict hierarchy:
    Developer/System Policy > Permission Policy > Tool/Action Authorization > LLM Suggestion.

    Rules:
    - Only `AUTHORIZED` satisfies a required permission.
    - `NOT_DETERMINED`, `DENIED`, `RESTRICTED`, and `UNAVAILABLE` strictly block access.
    - Public context is accessible without permission.
    - Private context is accessible unless a declared required permission is unauthorized.
    - Sensitive context with a required permission is blocked unless explicitly AUTHORIZED.
    """

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self.permission_manager = permission_manager or default_permission_manager

    async def is_context_allowed(
        self,
        item: Union[ContextBlock, BaseContextProvider],
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> Tuple[bool, Optional[str]]:
        """
        Evaluates whether the given context block or context provider is permitted.
        Returns (is_allowed: bool, reason_if_blocked: Optional[str]).
        """
        required_perm = item.required_permission
        sensitivity = item.sensitivity

        # 1. Enforce explicit required permission check
        if required_perm is not None:
            is_auth = await self.permission_manager.is_authorized(required_perm, user_id=user_id)
            if not is_auth:
                status = await self.permission_manager.get_status(required_perm, user_id=user_id)
                perm_name = required_perm.value if hasattr(required_perm, "value") else str(required_perm)
                reason = f"Context access denied: Required permission '{perm_name}' is not authorized (status: '{status.value}')."
                logger.info("Context blocked: %s", reason)
                return False, reason

        # 2. Sensitivity-specific checks
        if sensitivity == ContextSensitivity.PUBLIC:
            return True, None

        if sensitivity == ContextSensitivity.PRIVATE:
            return True, None

        if sensitivity == ContextSensitivity.SENSITIVE:
            # Sensitive context must have an explicit authorized permission if one was required
            return True, None

        return True, None


default_context_access_policy = ContextAccessPolicy()
