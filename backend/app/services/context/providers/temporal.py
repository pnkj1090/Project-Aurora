from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional, Callable, Any
import logging

from app.services.context.base import BaseContextProvider, ContextBlock

logger = logging.getLogger("aurora.context.temporal")


class TemporalContextProvider(BaseContextProvider):
    """
    Supplies current temporal context (date, weekday, UTC time, and local time).
    Avoids hard-coding any specific region, defaulting to UTC with configurable user timezone support.
    """

    def __init__(
        self,
        default_timezone: str = "UTC",
        now_fn: Optional[Callable[[], datetime]] = None,
        priority: int = 10,
    ):
        self.default_timezone = default_timezone
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._priority = priority

    @property
    def name(self) -> str:
        return "temporal"

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def context_type(self) -> str:
        return "temporal"

    @property
    def source(self) -> str:
        return "system"

    @property
    def sensitivity(self) -> ContextSensitivity:
        from app.services.context.base import ContextSensitivity
        return ContextSensitivity.PUBLIC

    def _resolve_zoneinfo(self, tz_name: Optional[str]) -> tuple[ZoneInfo, str]:
        """Resolve a timezone string into ZoneInfo, falling back safely to UTC if invalid."""
        target = (tz_name or self.default_timezone or "UTC").strip()
        try:
            return ZoneInfo(target), target
        except (ZoneInfoNotFoundError, ValueError, Exception) as exc:
            logger.debug("Timezone '%s' not recognized (%s). Falling back to UTC.", target, exc)
            return ZoneInfo("UTC"), "UTC"

    async def get_context(
        self,
        session: Optional[Any] = None,
        query: Optional[str] = None,
        **kwargs: Any
    ) -> Optional[ContextBlock]:
        utc_now = self.now_fn()
        if utc_now.tzinfo is None:
            utc_now = utc_now.replace(tzinfo=timezone.utc)
        else:
            utc_now = utc_now.astimezone(timezone.utc)

        # Allow timezone extraction from session metadata or kwargs
        tz_override = kwargs.get("user_timezone")
        if not tz_override and session and hasattr(session, "metadata") and isinstance(session.metadata, dict):
            tz_override = session.metadata.get("timezone") or session.metadata.get("user_timezone")

        local_zone, tz_display = self._resolve_zoneinfo(tz_override)
        local_now = utc_now.astimezone(local_zone)

        date_str = local_now.strftime("%A, %B %d, %Y")
        weekday_str = local_now.strftime("%A")
        utc_time_str = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")
        local_time_str = local_now.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"- Current Date: {date_str}",
            f"- Weekday: {weekday_str}",
            f"- Current UTC Time: {utc_time_str}",
        ]
        if tz_display != "UTC":
            lines.append(f"- User Local Time: {local_time_str} ({tz_display})")
        else:
            lines.append(f"- Reference Timezone: UTC")

        content = "\n".join(lines)
        from app.services.context.base import ContextSensitivity
        return ContextBlock(
            title="Temporal Reference",
            content=content,
            priority=self.priority,
            sensitivity=ContextSensitivity.PUBLIC,
            source="system",
            freshness=utc_now,
            metadata={
                "utc_now": utc_now.isoformat(),
                "local_now": local_now.isoformat(),
                "timezone": tz_display,
                "weekday": weekday_str
            }
        )
