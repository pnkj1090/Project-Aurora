import logging
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.permissions.models import PermissionType
from app.services.permissions import (
    PermissionManager,
    get_permission_manager_dependency,
)
from app.services.tasks import (
    Task,
    TaskStatus,
    TaskPriority,
    TaskManager,
    get_task_manager_dependency,
)

logger = logging.getLogger("aurora.api.tasks")
router = APIRouter()


# MARK: - Request & Response Schemas

class TaskResponse(BaseModel):
    id: str
    user_id: str
    title: str
    description: str
    status: TaskStatus
    priority: TaskPriority
    created_at: datetime
    updated_at: datetime
    due_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tags: List[str]
    is_active: bool
    source_commitment_id: Optional[str] = None


class TaskListResponse(BaseModel):
    items: List[TaskResponse]
    total: int
    limit: int
    offset: int


class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=500, description="Task title.")
    description: Optional[str] = Field(default="", max_length=5000, description="Task description.")
    priority: TaskPriority = Field(default=TaskPriority.MEDIUM, description="Task priority.")
    due_at: Optional[datetime] = Field(default=None, description="Optional UTC due datetime.")
    tags: List[str] = Field(default_factory=list, description="Categorization tags.")
    source_commitment_id: Optional[str] = Field(default=None, description="Optional linked commitment ID.")


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Updated task title.")
    description: Optional[str] = Field(None, max_length=5000, description="Updated task description.")
    priority: Optional[TaskPriority] = Field(None, description="Updated task priority.")
    due_at: Optional[datetime] = Field(None, description="Updated UTC due datetime.")
    tags: Optional[List[str]] = Field(None, description="Updated categorization tags.")
    source_commitment_id: Optional[str] = Field(None, description="Updated linked commitment ID.")
    clear_commitment: Optional[bool] = Field(False, description="Clear linked commitment.")


class TaskDeleteResponse(BaseModel):
    id: str
    status: str
    deleted: bool


def _item_to_response(item: Task) -> TaskResponse:
    return TaskResponse(
        id=item.id,
        user_id=item.user_id,
        title=item.title,
        description=item.description or "",
        status=item.status,
        priority=item.priority,
        created_at=item.created_at,
        updated_at=item.updated_at,
        due_at=item.due_at,
        completed_at=item.completed_at,
        tags=item.tags,
        is_active=item.is_active,
        source_commitment_id=item.source_commitment_id,
    )


async def get_current_user_id(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> str:
    return principal.user.user_id


async def require_user_id(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> str:
    return principal.user.user_id


async def require_tasks_permission(
    user_id: str = Depends(require_user_id),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
) -> str:
    """
    Enforces PermissionType.TASKS is AUTHORIZED for the active user.
    Fails closed with 403 Forbidden if not authorized.
    """
    is_auth = await permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tasks access is not authorized. The user must grant tasks permission."
        )
    return user_id


async def ensure_tasks_permission(user_id: str, permission_manager: PermissionManager) -> None:
    if not await permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id):
        raise HTTPException(status_code=403, detail="Tasks access is not authorized. The user must grant tasks permission.")


# MARK: - Endpoints

@router.get("", response_model=TaskListResponse)
async def list_tasks(
    limit: int = Query(50, ge=1, le=100, description="Number of tasks to return."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    status_filter: Optional[TaskStatus] = Query(None, alias="status", description="Filter by TaskStatus."),
    priority_filter: Optional[TaskPriority] = Query(None, alias="priority", description="Filter by TaskPriority."),
    q: Optional[str] = Query(None, min_length=1, max_length=100, description="Search query."),
    include_inactive: bool = Query(False, description="Whether to include inactive/deleted tasks."),
    user_id: str = Depends(get_current_user_id),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    List tasks strictly scoped to the active user identity.
    Never queries all tasks and filters afterwards.
    """
    statuses = [status_filter] if status_filter else None
    priorities = [priority_filter] if priority_filter else None

    items = await manager.list_tasks(
        user_id=user_id,
        statuses=statuses,
        priorities=priorities,
        query=q,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )

    total = await manager.count_tasks(
        user_id=user_id,
        statuses=statuses,
        priorities=priorities,
        query=q,
        include_inactive=include_inactive,
    )

    return TaskListResponse(
        items=[_item_to_response(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: TaskCreateRequest,
    user_id: str = Depends(require_tasks_permission),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Create a new actionable task for the active user identity.
    Arbitrary metadata and source_memory_id are not accepted in public REST creation.
    """
    try:
        task = await manager.create_task(
            title=request.title,
            user_id=user_id,
            description=request.description,
            priority=request.priority,
            due_at=request.due_at,
            tags=request.tags,
            source_commitment_id=request.source_commitment_id,
        )
        return _item_to_response(task)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    include_inactive: bool = Query(False, description="Whether to return inactive/deleted tasks."),
    user_id: str = Depends(get_current_user_id),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Retrieve a task by ID strictly scoped to the user.
    Returns HTTP 404 if not found or inactive.
    """
    task = await manager.get_task(task_id=task_id, user_id=user_id)
    if task is None or (not include_inactive and not task.is_active):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return _item_to_response(task)


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    request: TaskUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Update permitted user fields on an active task.
    Does NOT allow status changes (use dedicated /complete or /cancel endpoints).
    """
    user_id = principal.user.user_id
    existing = await manager.get_task(task_id=task_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active task '{task_id}' not found.",
        )
    await ensure_tasks_permission(user_id, permission_manager)

    try:
        updated = await manager.update_task(
            task_id=task_id,
            user_id=user_id,
            title=request.title,
            description=request.description,
            priority=request.priority,
            due_at=request.due_at,
            tags=request.tags,
            source_commitment_id=request.source_commitment_id,
            clear_commitment=request.clear_commitment or False,
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task '{task_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Dedicated lifecycle endpoint to mark a task completed.
    Does NOT soft delete the item (is_active remains True).
    """
    user_id = principal.user.user_id
    task = await manager.get_task(task_id=task_id, user_id=user_id)
    if task is None or not task.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active task '{task_id}' not found.",
        )
    await ensure_tasks_permission(user_id, permission_manager)

    try:
        updated = await manager.complete_task(task_id=task_id, user_id=user_id)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task '{task_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/{task_id}/start", response_model=TaskResponse)
async def start_task(
    task_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """Move a pending task into the existing in_progress lifecycle state."""
    user_id = principal.user.user_id
    task = await manager.get_task(task_id=task_id, user_id=user_id)
    if task is None or not task.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Active task '{task_id}' not found.")
    await ensure_tasks_permission(user_id, permission_manager)
    try:
        started = await manager.start_task(task_id=task_id, user_id=user_id)
        if started is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{task_id}' not found.")
        return _item_to_response(started)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Dedicated lifecycle endpoint to mark a task cancelled.
    Does NOT soft delete the item (is_active remains True).
    """
    user_id = principal.user.user_id
    task = await manager.get_task(task_id=task_id, user_id=user_id)
    if task is None or not task.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active task '{task_id}' not found.",
        )
    await ensure_tasks_permission(user_id, permission_manager)

    try:
        updated = await manager.cancel_task(task_id=task_id, user_id=user_id)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task '{task_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/{task_id}", response_model=TaskDeleteResponse)
async def delete_task(
    task_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Safe soft deletion (is_active = False).
    Does NOT mark the task completed or cancelled.
    """
    user_id = principal.user.user_id
    existing = await manager.get_task(task_id=task_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active task '{task_id}' not found.",
        )
    await ensure_tasks_permission(user_id, permission_manager)

    success = await manager.delete_task(task_id=task_id, user_id=user_id, soft=True)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Failed to delete task '{task_id}'.",
        )

    return TaskDeleteResponse(
        id=task_id,
        status="deleted",
        deleted=True,
    )


@router.post("/{task_id}/link/{commitment_id}", response_model=TaskResponse)
async def link_task(
    task_id: str,
    commitment_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Link a task to a commitment under active user identity.
    """
    user_id = principal.user.user_id
    task = await manager.get_task(task_id=task_id, user_id=user_id)
    if task is None or not task.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active task '{task_id}' not found.",
        )
    await ensure_tasks_permission(user_id, permission_manager)
    try:
        updated, _ = await manager.link_task_commitment(
            task_id=task_id,
            commitment_id=commitment_id,
            user_id=user_id,
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task '{task_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if "does not exist" in str(e) else status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.post("/{task_id}/unlink", response_model=TaskResponse)
async def unlink_task(
    task_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: TaskManager = Depends(get_task_manager_dependency),
):
    """
    Unlink a task from its commitment.
    """
    user_id = principal.user.user_id
    task = await manager.get_task(task_id=task_id, user_id=user_id)
    if task is None or not task.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active task '{task_id}' not found.",
        )
    await ensure_tasks_permission(user_id, permission_manager)
    updated, _ = await manager.unlink_task_commitment(
        task_id=task_id,
        user_id=user_id,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found.",
        )
    return _item_to_response(updated)
