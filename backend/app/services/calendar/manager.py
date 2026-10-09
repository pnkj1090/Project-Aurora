import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.services.calendar.base import BaseCalendarManager
from app.services.calendar.models import CalendarEvent
from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager

logger = logging.getLogger("aurora.services.calendar")

MAX_SNAPSHOT_EVENTS = 50
MAX_SNAPSHOT_SPAN_DAYS = 60
MAX_QUERY_RANGE_DAYS = 30
MAX_QUERY_RESULTS = 50
DEFAULT_QUERY_RESULTS = 10


class CalendarManager(BaseCalendarManager):
    """
    Orchestration layer governing ephemeral calendar snapshots, permission gating,
    bounds validation, deterministic ordering, and user isolation.
    Strictly in-memory: does not persist calendar history or events to database.
    """

    def __init__(self, permission_manager: Optional[PermissionManager] = None):
        self._permission_manager = permission_manager or default_permission_manager
        # Ephemeral in-memory store: user_id -> List[CalendarEvent]
        self._events: Dict[str, List[CalendarEvent]] = {}
        # Ephemeral in-memory store: user_id -> datetime
        self._snapshot_timestamps: Dict[str, datetime] = {}

    @property
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager

    async def is_calendar_authorized(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> bool:
        """Checks if PermissionType.CALENDAR is currently AUTHORIZED for the active user."""
        return await self._permission_manager.is_authorized(
            PermissionType.CALENDAR,
            user_id=user_id
        )

    async def set_events(
        self,
        events: List[CalendarEvent],
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> List[CalendarEvent]:
        """
        Ingests an ephemeral calendar snapshot for the user identity.
        Replaces any prior snapshot. Does not retain historical events.
        Never logs event titles or location strings.
        """
        # 1. Authorization check
        is_auth = await self.is_calendar_authorized(user_id=user_id)
        if not is_auth:
            logger.info("Calendar snapshot ingestion blocked: user '%s' not authorized", user_id)
            raise PermissionError("Calendar access is not authorized for this user")

        # 2. Maximum event count enforcement at ingestion
        if len(events) > MAX_SNAPSHOT_EVENTS:
            raise ValueError(f"Calendar snapshot exceeds maximum allowed of {MAX_SNAPSHOT_EVENTS} events")

        # 3. Duplicate event ID check within the snapshot
        seen_ids = set()
        for ev in events:
            if ev.event_id in seen_ids:
                raise ValueError(f"Duplicate event ID '{ev.event_id}' detected in calendar snapshot")
            seen_ids.add(ev.event_id)

        # 4. User isolation and date span validation
        if events:
            min_start = min(e.start_at for e in events)
            max_end = max(e.end_at for e in events)
            if (max_end - min_start).days > MAX_SNAPSHOT_SPAN_DAYS:
                raise ValueError(f"Calendar snapshot date span exceeds maximum allowed of {MAX_SNAPSHOT_SPAN_DAYS} days")

            for ev in events:
                if ev.user_id != user_id and ev.user_id != DEFAULT_LOCAL_USER_ID:
                    raise ValueError(
                        f"Cross-user calendar injection rejected: event user_id '{ev.user_id}' does not match active user '{user_id}'"
                    )

        # Clone and normalize user_id
        stored_events: List[CalendarEvent] = []
        for ev in events:
            cloned = ev.model_copy(deep=True)
            cloned.user_id = user_id
            stored_events.append(cloned)

        # Deterministic sort: start_at ascending, event_id tie-breaker
        stored_events.sort(key=lambda e: (e.start_at.astimezone(timezone.utc), e.event_id))

        self._events[user_id] = stored_events
        self._snapshot_timestamps[user_id] = datetime.now(timezone.utc)
        logger.info("Ingested %d ephemeral calendar events for user '%s'", len(stored_events), user_id)
        return stored_events

    async def get_events(
        self,
        start_at: datetime,
        end_at: datetime,
        max_results: int = DEFAULT_QUERY_RESULTS,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Tuple[Optional[List[CalendarEvent]], str]:
        """
        Retrieves events overlapping the requested date range for the active user if authorized.
        Returns: (events: Optional[List[CalendarEvent]], status_reason: str)
        status_reason values: "available", "unauthorized"
        """
        # 1. Authorization check
        is_auth = await self.is_calendar_authorized(user_id=user_id)
        if not is_auth:
            logger.info("Calendar query blocked: user '%s' not authorized", user_id)
            return None, "unauthorized"

        # 2. Datetime validation
        if start_at.tzinfo is None or end_at.tzinfo is None:
            raise ValueError("start_at and end_at must be timezone-aware datetimes")

        if end_at < start_at:
            raise ValueError("end_at cannot be earlier than start_at")

        query_days = (end_at - start_at).total_seconds() / 86400.0
        if query_days > MAX_QUERY_RANGE_DAYS:
            raise ValueError(f"Query date range ({query_days:.1f} days) exceeds maximum allowed of {MAX_QUERY_RANGE_DAYS} days")

        limit = min(max(1, max_results), MAX_QUERY_RESULTS)

        user_events = self._events.get(user_id, [])

        # Normalize boundaries to UTC for comparison
        start_utc = start_at.astimezone(timezone.utc)
        end_utc = end_at.astimezone(timezone.utc)

        # Filter events overlapping [start_utc, end_utc]
        filtered: List[CalendarEvent] = []
        for ev in user_events:
            ev_start_utc = ev.start_at.astimezone(timezone.utc)
            ev_end_utc = ev.end_at.astimezone(timezone.utc)
            if ev_end_utc >= start_utc and ev_start_utc <= end_utc:
                filtered.append(ev.model_copy(deep=True))

        # Deterministic chronological order
        filtered.sort(key=lambda e: (e.start_at.astimezone(timezone.utc), e.event_id))

        return filtered[:limit], "available"

    def clear_events(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> None:
        """Clears the ephemeral snapshot for the user."""
        self._events.pop(user_id, None)
        self._snapshot_timestamps.pop(user_id, None)
        logger.info("Cleared ephemeral calendar events for user '%s'", user_id)

    def get_snapshot_timestamp(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> Optional[datetime]:
        """Returns the ingestion timestamp of the active calendar snapshot for the user."""
        return self._snapshot_timestamps.get(user_id)

    def set_snapshot_timestamp(self, timestamp: datetime, user_id: str = DEFAULT_LOCAL_USER_ID) -> None:
        """Explicitly sets the snapshot timestamp (used for testing or synchronization)."""
        self._snapshot_timestamps[user_id] = timestamp

    def is_snapshot_available(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> bool:
        """Checks whether a calendar snapshot has been provided for the user."""
        return user_id in self._events

    def is_snapshot_fresh(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        max_age_seconds: int = 86400,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Evaluates whether the calendar snapshot is fresh (< max_age_seconds, default 24 hours).
        CalendarManager is the sole authority on calendar snapshot freshness.
        """
        ts = self._snapshot_timestamps.get(user_id)
        if ts is None:
            return False
        eval_now = now or datetime.now(timezone.utc)
        age = (eval_now - ts).total_seconds()
        return age <= max_age_seconds


# Global singleton instance
_default_calendar_manager: Optional[CalendarManager] = None


def get_calendar_manager(
    permission_manager: Optional[PermissionManager] = None
) -> CalendarManager:
    """Retrieve or construct the CalendarManager instance."""
    global _default_calendar_manager
    if permission_manager is not None:
        return CalendarManager(permission_manager=permission_manager)
    if _default_calendar_manager is None:
        _default_calendar_manager = CalendarManager()
    return _default_calendar_manager


def get_calendar_manager_dependency() -> CalendarManager:
    """FastAPI dependency provider."""
    return get_calendar_manager()
