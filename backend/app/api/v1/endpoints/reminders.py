import logging
from enum import Enum
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, Header, status
from pydantic import BaseModel, Field

from app.services.permissions.models import PermissionType
from app.services.permissions import (
    PermissionManager,
    get_permission_manager_dependency,
)
from app.services.reminders import (
    Reminder,
    ReminderStatus,
    ReminderManager,
    get_reminder_manager_dependency,
)

logger = logging.getLogger("aurora.api.reminders")
router = APIRouter()


# MARK: - Request & Response Schemas

class NotificationSyncSignal(str, Enum):
    NONE = "NONE"
    SCHEDULE = "SCHEDULE"
    RESCHEDULE = "RESCHEDULE"
    CANCEL = "CANCEL"


class ReminderResponse(BaseModel):
    id: str
    user_id: str
    title: str
    body: str
    scheduled_at: datetime
    status: ReminderStatus
    created_at: datetime
    updated_at: datetime
    task_id: Optional[str] = None
    commitment_id: Optional[str] = None
    notification_id: Optional[str] = None
    is_active: bool
    notification_sync: NotificationSyncSignal = NotificationSyncSignal.NONE


class ReminderListResponse(BaseModel):
    items: List[ReminderResponse]
    total: int
    limit: int
    offset: int


class ReminderCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500, description="Reminder title.")
    scheduled_at: datetime = Field(..., description="Target UTC datetime for reminder.")
    body: Optional[str] = Field(default="", max_length=5000, description="Optional reminder body.")
    task_id: Optional[str] = Field(default=None, description="Optional linked task ID.")
    commitment_id: Optional[str] = Field(default=None, description="Optional linked commitment ID.")


class ReminderUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Updated reminder title.")
    body: Optional[str] = Field(None, max_length=5000, description="Updated reminder body.")
    scheduled_at: Optional[datetime] = Field(None, description="Updated target UTC datetime.")
    task_id: Optional[str] = Field(None, description="Optional linked task ID (cannot be combined with commitment_id).")
    commitment_id: Optional[str] = Field(None, description="Optional linked commitment ID (cannot be combined with task_id).")
    clear_task: Optional[bool] = Field(False, description="Clear linked task.")
    clear_commitment: Optional[bool] = Field(False, description="Clear linked commitment.")


class ReminderSnoozeRequest(BaseModel):
    duration_minutes: int = Field(default=15, ge=5, le=1440, description="Minutes to snooze forward (5 to 1440).")


class ReminderDeleteResponse(BaseModel):
    id: str
    status: str
    deleted: bool
    notification_sync: NotificationSyncSignal = NotificationSyncSignal.CANCEL


def _item_to_response(
    item: Reminder,
    notification_sync: NotificationSyncSignal = NotificationSyncSignal.NONE
) -> ReminderResponse:
    return ReminderResponse(
        id=item.id,
        user_id=item.user_id,
        title=item.title,
        body=item.body or "",
        scheduled_at=item.scheduled_at,
        status=item.status,
        created_at=item.created_at,
        updated_at=item.updated_at,
        task_id=item.task_id,
        commitment_id=item.commitment_id,
        notification_id=item.notification_id,
        is_active=item.is_active,
        notification_sync=notification_sync,
    )


def get_current_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> str:
    """
    Enforces server-resolved user identity.
    Fails closed if X-User-ID header is missing or empty.
    Zero production DEFAULT_LOCAL_USER_ID fallback.
    """
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication identity header 'X-User-ID'."
        )
    return x_user_id.strip()


async def require_reminders_permission(
    user_id: str = Depends(get_current_user_id),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
) -> str:
    """
    Enforces PermissionType.REMINDERS is AUTHORIZED for the active user.
    Fails closed with 403 Forbidden if not authorized.
    """
    is_auth = await permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reminders access is not authorized. The user must grant reminders permission."
        )
    return user_id


# MARK: - Endpoints

@router.get("", response_model=ReminderListResponse)
async def list_reminders(
    limit: int = Query(50, ge=1, le=100, description="Number of reminders to return."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    status_filter: Optional[ReminderStatus] = Query(None, alias="status", description="Filter by ReminderStatus."),
    task_id: Optional[str] = Query(None, description="Filter by linked task ID."),
    commitment_id: Optional[str] = Query(None, description="Filter by linked commitment ID."),
    q: Optional[str] = Query(None, min_length=1, max_length=100, description="Search query."),
    include_inactive: bool = Query(False, description="Whether to include inactive/deleted reminders."),
    user_id: str = Depends(require_reminders_permission),
    manager: ReminderManager = Depends(get_reminder_manager_dependency),
):
    """
    List reminders strictly scoped to the active user identity.
    """
    statuses = [status_filter] if status_filter else None

    items = await manager.list_reminders(
        user_id=user_id,
        statuses=statuses,
        task_id=task_id,
        commitment_id=commitment_id,
        query=q,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )

    total = await manager.count_reminders(
        user_id=user_id,
        statuses=statuses,
        task_id=task_id,
        commitment_id=commitment_id,
        query=q,
        include_inactive=include_inactive,
    )

    return ReminderListResponse(
        items=[_item_to_response(item, notification_sync=NotificationSyncSignal.NONE) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    request: ReminderCreateRequest,
    user_id: str = Depends(require_reminders_permission),
    manager: ReminderManager = Depends(get_reminder_manager_dependency),
):
    """
    Create a new explicit reminder for the active user identity.
    Emits NotificationSyncSignal.SCHEDULE if reminder is scheduled.
    """
    try:
        reminder = await manager.create_reminder(
            title=request.title,
            scheduled_at=request.scheduled_at,
            user_id=user_id,
            body=request.body,
            task_id=request.task_id,
            commitment_id=request.commitment_id,
        )
        sync_signal = NotificationSyncSignal.SCHEDULE if reminder.status == ReminderStatus.SCHEDULED else NotificationSyncSignal.NONE
        return _item_to_response(reminder, notification_sync=sync_signal)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get("/{reminder_id}", response_model=ReminderResponse)
async def get_reminder(
    reminder_id: str,
    include_inactive: bool = Query(False, description="Whether to return inactive/deleted reminders."),
    user_id: str = Depends(require_reminders_permission),
    manager: ReminderManager = Depends(get_reminder_manager_dependency),
):
    """
    Retrieve a reminder by ID strictly scoped to the user.
    """
    reminder = await manager.get_reminder(reminder_id=reminder_id, user_id=user_id)
    if reminder is None or (not include_inactive and not reminder.is_active):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reminder '{reminder_id}' not found.",
        )
    return _item_to_response(reminder, notification_sync=NotificationSyncSignal.NONE)


@router.patch("/{reminder_id}", response_model=ReminderResponse)
async def update_reminder(
    reminder_id: str,
    request: ReminderUpdateRequest,
    user_id: str = Depends(require_reminders_permission),
    manager: ReminderManager = Depends(get_reminder_manager_dependency),
):
    """
    Update permitted user fields on an active reminder (title, body, scheduled_at, task_id, commitment_id).
    Does NOT allow status changes (use dedicated /cancel endpoint).
    Returns RESCHEDULE if title, body, or scheduled_at changed; NONE if metadata-only or no-op.
    """
    existing = await manager.get_reminder(reminder_id=reminder_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active reminder '{reminder_id}' not found.",
        )

    try:
        updated, mutated = await manager.update_reminder_with_status(
            reminder_id=reminder_id,
            user_id=user_id,
            title=request.title,
            body=request.body,
            scheduled_at=request.scheduled_at,
            task_id=request.task_id,
            commitment_id=request.commitment_id,
            clear_task=bool(request.clear_task),
            clear_commitment=bool(request.clear_commitment),
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Reminder '{reminder_id}' not found.",
            )

        sync_signal = NotificationSyncSignal.NONE
        if mutated:
            orig_sched = existing.scheduled_at if existing.scheduled_at.tzinfo else existing.scheduled_at.replace(tzinfo=timezone.utc)
            new_sched = updated.scheduled_at if updated.scheduled_at.tzinfo else updated.scheduled_at.replace(tzinfo=timezone.utc)
            schedule_changed = (orig_sched != new_sched)
            title_changed = (existing.title != updated.title)
            body_changed = ((existing.body or "") != (updated.body or ""))

            if schedule_changed or title_changed or body_changed:
                sync_signal = NotificationSyncSignal.RESCHEDULE

        return _item_to_response(updated, notification_sync=sync_signal)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.post("/{reminder_id}/snooze", response_model=ReminderResponse)
async def snooze_reminder(
    reminder_id: str,
    request: ReminderSnoozeRequest = ReminderSnoozeRequest(),
    user_id: str = Depends(require_reminders_permission),
    manager: ReminderManager = Depends(get_reminder_manager_dependency),
):
    """
    Dedicated endpoint to snooze a reminder forward.
    Returns RESCHEDULE when trigger time changes; NONE if no-op.
    """
    existing = await manager.get_reminder(reminder_id=reminder_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active reminder '{reminder_id}' not found.",
        )

    try:
        updated, mutated = await manager.snooze_reminder(
            reminder_id=reminder_id,
            user_id=user_id,
            duration_minutes=request.duration_minutes
        )
        sync_signal = NotificationSyncSignal.RESCHEDULE if mutated else NotificationSyncSignal.NONE
        return _item_to_response(updated, notification_sync=sync_signal)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/{reminder_id}/cancel", response_model=ReminderResponse)
async def cancel_reminder(
    reminder_id: str,
    user_id: str = Depends(require_reminders_permission),
    manager: ReminderManager = Depends(get_reminder_manager_dependency),
):
    """
    Dedicated lifecycle endpoint to cancel a reminder.
    Returns CANCEL on actual cancellation; NONE if already cancelled.
    """
    existing = await manager.get_reminder(reminder_id=reminder_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active reminder '{reminder_id}' not found.",
        )

    try:
        updated, mutated = await manager.cancel_reminder_with_status(reminder_id=reminder_id, user_id=user_id)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Reminder '{reminder_id}' not found.",
            )
        sync_signal = NotificationSyncSignal.CANCEL if mutated else NotificationSyncSignal.NONE
        return _item_to_response(updated, notification_sync=sync_signal)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/{reminder_id}", response_model=ReminderDeleteResponse)
async def delete_reminder(
    reminder_id: str,
    user_id: str = Depends(require_reminders_permission),
    manager: ReminderManager = Depends(get_reminder_manager_dependency),
):
    """
    Safe soft deletion (is_active = False).
    Returns CANCEL if an active scheduled notification was removed.
    """
    existing = await manager.get_reminder(reminder_id=reminder_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active reminder '{reminder_id}' not found.",
        )

    was_scheduled = (existing.status == ReminderStatus.SCHEDULED)
    success = await manager.delete_reminder(reminder_id=reminder_id, user_id=user_id, soft=True)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Failed to delete reminder '{reminder_id}'.",
        )

    sync_signal = NotificationSyncSignal.CANCEL if was_scheduled else NotificationSyncSignal.NONE
    return ReminderDeleteResponse(
        id=reminder_id,
        status="deleted",
        deleted=True,
        notification_sync=sync_signal,
    )
