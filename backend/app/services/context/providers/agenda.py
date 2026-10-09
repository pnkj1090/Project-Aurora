from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional, Any

from app.services.context.base import BaseContextProvider, ContextBlock, ContextSensitivity
from app.services.permissions.models import PermissionType
from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.agenda import AgendaService, get_agenda_service

logger = logging.getLogger("aurora.context.agenda")

_AGENDA_KEYWORD_RE = re.compile(
    r"\b(agenda|schedule|today|plan|conflict|free|busy|whats next|what's next)\b",
    re.IGNORECASE
)


class AgendaContextProvider(BaseContextProvider):
    """
    Supplies a synthesized, unified daily agenda projection to the ContextEngine.
    Classified as PROTECTED_PERSONAL.
    Strictly capped at 250 tokens (hard character limit <= 1000 chars).
    Relevance-gated: only activated when the query indicates schedule/agenda intent.
    Completely failure-isolated: any exception returns None without disrupting prompt assembly.
    """

    def __init__(
        self,
        agenda_service: Optional[AgendaService] = None,
        max_chars: int = 1000,
        priority: int = 29,
    ):
        self._agenda_service = agenda_service
        self._max_chars = min(max_chars, 1000)
        self._priority = priority

    @property
    def max_chars(self) -> int:
        return self._max_chars

    @property
    def service(self) -> AgendaService:
        return self._agenda_service or get_agenda_service()

    @property
    def name(self) -> str:
        return "agenda"

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def context_type(self) -> str:
        return "agenda"

    @property
    def source(self) -> str:
        return "phase6d/agenda"

    @property
    def sensitivity(self) -> ContextSensitivity:
        return ContextSensitivity.SENSITIVE

    @property
    def required_permission(self) -> Optional[PermissionType]:
        # Evaluates domain permissions dynamically inside AgendaService
        return None

    def _resolve_user_id(self, session: Optional[Any] = None, **kwargs: Any) -> str:
        if kwargs.get("user_id"):
            return str(kwargs["user_id"])
        if session and hasattr(session, "user_id") and session.user_id:
            return str(session.user_id)
        return DEFAULT_LOCAL_USER_ID

    async def get_context(
        self,
        session: Optional[Any] = None,
        query: Optional[str] = None,
        **kwargs: Any
    ) -> Optional[ContextBlock]:
        return await self.provide_context(query=query, session=session, **kwargs)

    async def provide_context(
        self,
        query: Optional[str] = None,
        session: Optional[Any] = None,
        **kwargs: Any,
    ) -> Optional[ContextBlock]:
        """
        Synthesizes today's agenda if relevant.
        Failure-isolated: returns None on any error.
        """
        # Relevance gate
        clean_query = (query or "").strip()
        if clean_query and not _AGENDA_KEYWORD_RE.search(clean_query):
            # If query exists but doesn't match agenda keywords, skip provider
            return None

        user_id = self._resolve_user_id(session, **kwargs)

        try:
            # Explicitly query today's agenda
            agenda = await self.service.get_daily_agenda(
                user_id=user_id,
                target_date=None,
                include_conflicts=True,
                include_free_slots=True,
            )

            lines = [f"[Today's Agenda ({agenda.target_date} - {agenda.timezone})]"]

            # Format items (data-minimized, public-safe IDs)
            item_count = 0
            for item in agenda.items:
                if item_count >= 8:
                    lines.append("... [remaining items omitted for length]")
                    break

                conflict_marker = ""
                if item.conflict_ids:
                    conflict_marker = f" <! CONFLICT ({len(item.conflict_ids)}) !>"

                if item.temporal_kind.value == "interval":
                    st = item.start_at.strftime("%H:%M") if item.start_at else ""
                    et = item.end_at.strftime("%H:%M") if item.end_at else ""
                    all_day_tag = " (All-Day)" if item.is_all_day else ""
                    lines.append(f"- {st}-{et} [Event]{all_day_tag} {item.display_title} ({item.source_entity_id}){conflict_marker}")
                elif item.temporal_kind.value == "instant":
                    inst = item.instant_at.strftime("%H:%M") if item.instant_at else ""
                    tag = "Task Deadline" if item.source_domain == "tasks" else "Reminder"
                    prio = f" [{item.priority.upper()}]" if item.priority else ""
                    lines.append(f"- {inst} [{tag}]{prio} {item.display_title} ({item.source_entity_id}){conflict_marker}")
                elif item.temporal_kind.value == "date_target":
                    lines.append(f"- [Target Horizon] {item.display_title} ({item.source_entity_id})")

                item_count += 1

            # Format free slots if any
            if agenda.free_slots:
                slot_strs = []
                for s in agenda.free_slots[:3]:
                    st = s.start_at.strftime("%H:%M")
                    et = s.end_at.strftime("%H:%M")
                    slot_strs.append(f"{st}-{et} ({s.duration_minutes}m)")
                lines.append(f"Available working gaps: {', '.join(slot_strs)}")

            # Format conflicts summary
            if agenda.conflicts:
                warning_conflicts = [c for c in agenda.conflicts if c.severity.value == "warning"]
                if warning_conflicts:
                    lines.append(f"Active Schedule Conflicts ({len(warning_conflicts)}):")
                    for c in warning_conflicts[:2]:
                        lines.append(f"  * {c.description}")

            content = "\n".join(lines)
            if len(content) > self.max_chars:
                content = content[:self.max_chars].rstrip() + "\n... [budget limit reached]"

            return ContextBlock(
                title="Daily Agenda & Schedule",
                content=content,
                priority=self.priority,
                sensitivity=self.sensitivity,
                source=self.source,
                is_user_visible=True,
            )
        except Exception:
            logger.debug("AgendaContextProvider failed safely; omitting agenda context", exc_info=True)
            return None
