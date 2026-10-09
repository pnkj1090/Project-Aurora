import logging
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Header, Query, status
from pydantic import BaseModel, Field

from app.services.identity import DEFAULT_LOCAL_USER_ID, default_identity_manager
from app.services.calendar import (
    CalendarEvent,
    CalendarManager,
    get_calendar_manager_dependency,
    MAX_SNAPSHOT_EVENTS,
    MAX_QUERY_RESULTS,
)

logger = logging.getLogger("aurora.api.calendar")
router = APIRouter()


class CalendarEventResponseItem(BaseModel):
    event_id: str
    title: str
    start_at: datetime
    end_at: datetime
    all_day: bool
    calendar_name: Optional[str] = None
    location: Optional[str] = None
    notes_available: bool = False
    source: str = "ios_eventkit"
    user_id: str


class CalendarEventsResponse(BaseModel):
    status: str = "success"
    count: int
    events: List[CalendarEventResponseItem]


class CalendarEventIngestItem(BaseModel):
    event_id: str = Field(..., description="Unique event ID")
    title: str = Field(..., description="Event title")
    start_at: datetime = Field(..., description="Timezone-aware start datetime")
    end_at: datetime = Field(..., description="Timezone-aware end datetime")
    all_day: bool = Field(default=False)
    calendar_name: Optional[str] = None
    location: Optional[str] = None
    notes_available: bool = False
    source: Optional[str] = "ios_eventkit"
    user_id: Optional[str] = None


class CalendarSnapshotIngestRequest(BaseModel):
    events: List[CalendarEventIngestItem] = Field(default_factory=list)
    user_id: Optional[str] = Field(default=None, description="Optional caller-supplied user_id for validation")


def get_current_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> str:
    """Derives target identity from authenticated header or default local user."""
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()
    return default_identity_manager.get_current_user().user_id


@router.get("/events", response_model=CalendarEventsResponse)
async def get_calendar_events(
    start_at: datetime = Query(..., description="Timezone-aware start datetime"),
    end_at: datetime = Query(..., description="Timezone-aware end datetime"),
    max_results: int = Query(default=10, ge=1, le=MAX_QUERY_RESULTS, description="Maximum number of events"),
    user_id: str = Depends(get_current_user_id),
    manager: CalendarManager = Depends(get_calendar_manager_dependency),
):
    """
    Retrieve read-only calendar events for the active user within an explicit datetime range.
    Requires PermissionType.CALENDAR to be AUTHORIZED.
    """
    is_auth = await manager.is_calendar_authorized(user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Calendar access is not authorized. The user must grant calendar permission.",
        )

    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_at and end_at must be timezone-aware datetimes.",
        )

    try:
        events, reason = await manager.get_events(
            start_at=start_at,
            end_at=end_at,
            max_results=max_results,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    if events is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Calendar access is not authorized for this user.",
        )

    items = [
        CalendarEventResponseItem(
            event_id=ev.event_id,
            title=ev.title,
            start_at=ev.start_at,
            end_at=ev.end_at,
            all_day=ev.all_day,
            calendar_name=ev.calendar_name,
            location=ev.location,
            notes_available=ev.notes_available,
            source=ev.source,
            user_id=ev.user_id,
        )
        for ev in events
    ]

    return CalendarEventsResponse(status="success", count=len(items), events=items)


@router.post("/events", response_model=CalendarEventsResponse, status_code=status.HTTP_201_CREATED)
async def ingest_calendar_snapshot(
    request: CalendarSnapshotIngestRequest,
    user_id: str = Depends(get_current_user_id),
    manager: CalendarManager = Depends(get_calendar_manager_dependency),
):
    """
    Ingest current read-only EventKit snapshot into Aurora's ephemeral memory cache.
    CRITICAL SECURITY CONTRACT:
    - This endpoint does NOT create, modify, or delete calendar events in EventKit or anywhere on disk.
    - Active Aurora identity determines ownership. Client cannot inject events into another user's cache.
    - Cross-user calendar injection is strictly rejected.
    """
    is_auth = await manager.is_calendar_authorized(user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot ingest calendar snapshot: PermissionType.CALENDAR is not authorized for this user.",
        )

    # Validate caller-supplied user_id against active identity
    if request.user_id and request.user_id.strip() and request.user_id.strip() != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cross-user calendar injection rejected: request user_id '{request.user_id}' does not match authenticated user '{user_id}'.",
        )

    # Validate individual items
    domain_events: List[CalendarEvent] = []
    for item in request.events:
        if item.user_id and item.user_id.strip() and item.user_id.strip() != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cross-user calendar injection rejected: event user_id '{item.user_id}' does not match authenticated user '{user_id}'.",
            )

        try:
            ev = CalendarEvent(
                event_id=item.event_id,
                title=item.title,
                start_at=item.start_at,
                end_at=item.end_at,
                all_day=item.all_day,
                calendar_name=item.calendar_name,
                location=item.location,
                notes_available=item.notes_available,
                source=item.source or "ios_eventkit",
                user_id=user_id,
            )
            domain_events.append(ev)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid calendar event: {exc}",
            )

    try:
        saved = await manager.set_events(domain_events, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    items = [
        CalendarEventResponseItem(
            event_id=ev.event_id,
            title=ev.title,
            start_at=ev.start_at,
            end_at=ev.end_at,
            all_day=ev.all_day,
            calendar_name=ev.calendar_name,
            location=ev.location,
            notes_available=ev.notes_available,
            source=ev.source,
            user_id=ev.user_id,
        )
        for ev in saved
    ]

    return CalendarEventsResponse(status="success", count=len(items), events=items)
