from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Tuple

from app.services.calendar.models import CalendarEvent


class BaseCalendarManager(ABC):
    """
    Abstract interface governing ephemeral calendar operations.
    """

    @abstractmethod
    async def is_calendar_authorized(self, user_id: str) -> bool:
        """Checks if PermissionType.CALENDAR is AUTHORIZED for the given user."""
        pass

    @abstractmethod
    async def set_events(self, events: List[CalendarEvent], user_id: str) -> List[CalendarEvent]:
        """Ingests an ephemeral calendar snapshot for the user."""
        pass

    @abstractmethod
    async def get_events(
        self,
        start_at: datetime,
        end_at: datetime,
        max_results: int = 10,
        user_id: str = "default_user",
    ) -> Tuple[Optional[List[CalendarEvent]], str]:
        """Retrieves events matching the requested date range."""
        pass

    @abstractmethod
    def clear_events(self, user_id: str) -> None:
        """Clears all ephemeral events for the user."""
        pass
