"""
Phase 5H — ProactiveContextProvider.

Core Invariants:
- STRICTLY OPT-IN: returns None unless an explicit proactive relevance signal is present.
- Generic conversation never automatically receives proactive candidates.
- Maximum 5 candidates (MAX_CONTEXT_CANDIDATES = 5).
- Exposes only ELIGIBLE candidates.
- Suppressed, expired, dismissed, and cancelled candidates are strictly excluded.
- ZERO leakage of internal metadata or reference_id.
- Sensitivity: ContextSensitivity.PRIVATE.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from app.services.context.base import BaseContextProvider, ContextBlock, ContextSensitivity
from app.services.permissions.models import PermissionType
from app.services.proactive.manager import ProactiveManager, get_proactive_manager
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveStatus,
    MAX_CONTEXT_CANDIDATES,
)

logger = logging.getLogger("aurora.context.providers.proactive")

PROACTIVE_INTENT_PHRASES = (
    "proactive",
    "suggestion",
    "suggestions",
    "recommendation",
    "recommendations",
    "needs my attention",
    "what should aurora remind",
    "proposals",
    "proactive reminders",
    "proactive assistance",
)


class ProactiveContextProvider(BaseContextProvider):
    """
    Opt-in context provider for active, eligible proactive proposals.
    """

    def __init__(
        self,
        proactive_manager: Optional[ProactiveManager] = None,
        max_candidates: int = MAX_CONTEXT_CANDIDATES,
        priority: int = 45,
    ) -> None:
        self._manager = proactive_manager
        self.max_candidates = min(max_candidates, MAX_CONTEXT_CANDIDATES)
        self._priority = priority

    @property
    def manager(self) -> ProactiveManager:
        return self._manager or get_proactive_manager()

    @property
    def name(self) -> str:
        return "proactive"

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def context_type(self) -> str:
        return "proactive"

    @property
    def source(self) -> str:
        return "system/proactive"

    @property
    def sensitivity(self) -> ContextSensitivity:
        return ContextSensitivity.PRIVATE

    @property
    def required_permission(self) -> Optional[PermissionType]:
        return None

    def _resolve_user_id(self, session: Optional[Any] = None, **kwargs: Any) -> Optional[str]:
        if kwargs.get("user_id"):
            uid = str(kwargs["user_id"]).strip()
            if uid:
                return uid
        if session and hasattr(session, "user_id") and session.user_id:
            uid = str(session.user_id).strip()
            if uid:
                return uid
        if session and hasattr(session, "metadata") and isinstance(session.metadata, dict):
            uid = str(session.metadata.get("user_id") or "").strip()
            if uid:
                return uid
        return None

    def _has_explicit_proactive_intent(self, **kwargs: Any) -> bool:
        """
        Verify whether the request contains an explicit proactive intent.
        Generic queries like 'hello', 'what is the weather', 'help me' return False.
        """
        if kwargs.get("proactive_query"):
            return True

        query = kwargs.get("query")
        if not query or not isinstance(query, str):
            return False

        q_lower = query.strip().lower()
        return any(phrase in q_lower for phrase in PROACTIVE_INTENT_PHRASES)

    async def get_context(
        self,
        session: Optional[Any] = None,
        **kwargs: Any,
    ) -> Optional[ContextBlock]:
        """
        Retrieve eligible proactive proposals if explicit intent is present.
        """
        if not self._has_explicit_proactive_intent(**kwargs):
            logger.debug("ProactiveContextProvider skipped: no explicit proactive intent.")
            return None

        user_id = self._resolve_user_id(session, **kwargs)
        if not user_id:
            logger.warning("ProactiveContextProvider: No server-controlled user identity found; failing closed.")
            return None

        # Retrieve eligible candidates only (with lazy sweep)
        candidates, _ = await self.manager.list_candidates(
            user_id=user_id,
            status=ProactiveStatus.ELIGIBLE,
            limit=self.max_candidates,
            offset=0,
            include_expired=False,
        )

        if not candidates:
            return None

        # Build formatted lines with safe fields only (no reference_id, no metadata)
        lines: List[str] = [f"Proactive Suggestions ({len(candidates)}):"]
        for c in candidates:
            lines.append(f"- [{c.priority.value.upper()}] {c.title}: {c.message}")

        content = "\n".join(lines)

        return ContextBlock(
            title="Proactive Suggestions",
            content=content,
            priority=self.priority,
            sensitivity=self.sensitivity,
            source=self.source,
            is_user_visible=True,
            required_permission=self.required_permission,
            metadata={"count": len(candidates)},
        )
