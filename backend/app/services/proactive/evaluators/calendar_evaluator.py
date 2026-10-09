"""
Phase 5I — CalendarProposalEvaluator.

Inspects calendar snapshot on-demand and deterministically generates OPEN_CALENDAR_EVENT proposals:
- Event starting within 60 minutes (now_utc <= start_at <= now_utc + 60m):
  OPEN_CALENDAR_EVENT, HIGH priority, CALENDAR_EVENT trigger.

Invariants:
- Strictly checks PermissionType.CALENDAR. If not authorized, returns [] immediately.
- Zero calendar data leaked when unauthorized.
- Evaluates strictly on-demand; no background daemon or polling.
- Reuses existing CalendarManager.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.calendar import CalendarManager, get_calendar_manager
from app.services.proactive.evaluators.base import BaseSubsystemEvaluator
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveAction,
    ProactivePriority,
    ProactiveTrigger,
    ProactiveStatus,
)


class CalendarProposalEvaluator(BaseSubsystemEvaluator):
    """
    On-demand evaluator for calendar-related proactive suggestions.
    """

    def __init__(self, calendar_manager: Optional[CalendarManager] = None):
        self._calendar_manager = calendar_manager

    @property
    def calendar_manager(self) -> CalendarManager:
        return self._calendar_manager or get_calendar_manager()

    async def evaluate_proposals(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> List[ProactiveCandidate]:
        # 1. Strict permission check
        is_auth = await self.calendar_manager.is_calendar_authorized(user_id=user_id)
        if not is_auth:
            return []

        now_utc = datetime.now(timezone.utc)
        approaching_threshold = now_utc + timedelta(minutes=60)

        # Query events in [now_utc - 5m, approaching_threshold]
        events, status = await self.calendar_manager.get_events(
            start_at=now_utc - timedelta(minutes=5),
            end_at=approaching_threshold,
            max_results=20,
            user_id=user_id,
        )

        if status != "available" or not events:
            return []

        candidates: List[ProactiveCandidate] = []

        for ev in events:
            ev_start_utc = ev.start_at if ev.start_at.tzinfo else ev.start_at.replace(tzinfo=timezone.utc)

            # Event starting within next 60 minutes
            if now_utc <= ev_start_utc <= approaching_threshold:
                candidate = ProactiveCandidate(
                    user_id=user_id,
                    source="calendar",
                    trigger=ProactiveTrigger.CALENDAR_EVENT,
                    action=ProactiveAction.OPEN_CALENDAR_EVENT,
                    priority=ProactivePriority.HIGH,
                    status=ProactiveStatus.PENDING,
                    title=f"Upcoming event: {ev.title}",
                    message=f"'{ev.title}' starts in less than an hour ({ev_start_utc.strftime('%H:%M UTC')}).",
                    reference_id=ev.event_id,
                    action_payload={"event_id": ev.event_id},
                )
                candidates.append(candidate)

        return candidates
