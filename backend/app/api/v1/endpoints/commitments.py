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
from app.services.commitments import (
    Commitment,
    CommitmentStatus,
    CommitmentPriority,
    CommitmentManager,
    get_commitment_manager_dependency,
)

logger = logging.getLogger("aurora.api.commitments")
router = APIRouter()


# MARK: - Request & Response Schemas

class CommitmentResponse(BaseModel):
    id: str
    user_id: str
    title: str
    description: str
    status: CommitmentStatus
    priority: CommitmentPriority
    created_at: datetime
    updated_at: datetime
    target_date: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    tags: List[str]
    is_active: bool


class CommitmentListResponse(BaseModel):
    items: List[CommitmentResponse]
    total: int
    limit: int
    offset: int


class CommitmentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=500, description="Commitment title.")
    description: Optional[str] = Field(default="", max_length=5000, description="Commitment description.")
    priority: CommitmentPriority = Field(default=CommitmentPriority.MEDIUM, description="Priority level.")
    target_date: Optional[datetime] = Field(default=None, description="Optional UTC target completion datetime.")
    tags: List[str] = Field(default_factory=list, description="Categorization tags.")


class CommitmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: Optional[str] = Field(None, min_length=1, max_length=500, description="Updated commitment title.")
    description: Optional[str] = Field(None, max_length=5000, description="Updated commitment description.")
    priority: Optional[CommitmentPriority] = Field(None, description="Updated commitment priority.")
    target_date: Optional[datetime] = Field(None, description="Updated UTC target completion datetime.")
    tags: Optional[List[str]] = Field(None, description="Updated categorization tags.")


class CommitmentDeleteResponse(BaseModel):
    id: str
    status: str
    deleted: bool


def _item_to_response(item: Commitment) -> CommitmentResponse:
    return CommitmentResponse(
        id=item.id,
        user_id=item.user_id,
        title=item.title,
        description=item.description or "",
        status=item.status,
        priority=item.priority,
        created_at=item.created_at,
        updated_at=item.updated_at,
        target_date=item.target_date,
        completed_at=item.completed_at,
        tags=item.tags,
        is_active=item.is_active,
    )


async def get_current_user_id(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> str:
    return principal.user.user_id


async def require_user_id(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)) -> str:
    return principal.user.user_id


async def require_commitments_permission(
    user_id: str = Depends(require_user_id),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
) -> str:
    """
    Enforces PermissionType.COMMITMENTS is AUTHORIZED for the active user.
    Fails closed with 403 Forbidden if not authorized.
    """
    is_auth = await permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Commitments access is not authorized. The user must grant commitments permission."
        )
    return user_id


async def ensure_commitments_permission(user_id: str, permission_manager: PermissionManager) -> None:
    if not await permission_manager.is_authorized(PermissionType.COMMITMENTS, user_id=user_id):
        raise HTTPException(status_code=403, detail="Commitments access is not authorized. The user must grant commitments permission.")


# MARK: - Endpoints

@router.get("", response_model=CommitmentListResponse)
async def list_commitments(
    limit: int = Query(50, ge=1, le=100, description="Number of commitments to return."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    status_filter: Optional[CommitmentStatus] = Query(None, alias="status", description="Filter by CommitmentStatus."),
    priority_filter: Optional[CommitmentPriority] = Query(None, alias="priority", description="Filter by CommitmentPriority."),
    q: Optional[str] = Query(None, min_length=1, max_length=100, description="Search query."),
    include_inactive: bool = Query(False, description="Whether to include inactive/deleted commitments."),
    user_id: str = Depends(get_current_user_id),
    manager: CommitmentManager = Depends(get_commitment_manager_dependency),
):
    """
    List commitments strictly scoped to the active user identity.
    Never queries all commitments and filters afterwards.
    """
    statuses = [status_filter] if status_filter else None
    priorities = [priority_filter] if priority_filter else None

    items = await manager.list_commitments(
        user_id=user_id,
        statuses=statuses,
        priorities=priorities,
        query=q,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )

    total = await manager.count_commitments(
        user_id=user_id,
        statuses=statuses,
        priorities=priorities,
        query=q,
        include_inactive=include_inactive,
    )

    return CommitmentListResponse(
        items=[_item_to_response(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=CommitmentResponse, status_code=status.HTTP_201_CREATED)
async def create_commitment(
    request: CommitmentCreateRequest,
    user_id: str = Depends(require_commitments_permission),
    manager: CommitmentManager = Depends(get_commitment_manager_dependency),
):
    """
    Create a new tracked commitment for the active user identity.
    Arbitrary metadata and source_memory_id are not accepted in public REST creation.
    """
    try:
        commitment = await manager.create_commitment(
            title=request.title,
            user_id=user_id,
            description=request.description,
            priority=request.priority,
            target_date=request.target_date,
            tags=request.tags,
        )
        return _item_to_response(commitment)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get("/{commitment_id}", response_model=CommitmentResponse)
async def get_commitment(
    commitment_id: str,
    include_inactive: bool = Query(False, description="Whether to return inactive/deleted commitments."),
    user_id: str = Depends(get_current_user_id),
    manager: CommitmentManager = Depends(get_commitment_manager_dependency),
):
    """
    Retrieve a commitment by ID strictly scoped to the user.
    Returns HTTP 404 if not found or inactive.
    """
    commitment = await manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
    if commitment is None or (not include_inactive and not commitment.is_active):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commitment '{commitment_id}' not found.",
        )
    return _item_to_response(commitment)


@router.patch("/{commitment_id}", response_model=CommitmentResponse)
async def update_commitment(
    commitment_id: str,
    request: CommitmentUpdateRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: CommitmentManager = Depends(get_commitment_manager_dependency),
):
    """
    Update permitted user fields on an active commitment.
    Does NOT allow status changes (use dedicated /complete or /cancel endpoints).
    """
    user_id = principal.user.user_id
    existing = await manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active commitment '{commitment_id}' not found.",
        )
    await ensure_commitments_permission(user_id, permission_manager)

    try:
        updated = await manager.update_commitment(
            commitment_id=commitment_id,
            user_id=user_id,
            title=request.title,
            description=request.description,
            priority=request.priority,
            target_date=request.target_date,
            tags=request.tags,
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Commitment '{commitment_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.post("/{commitment_id}/complete", response_model=CommitmentResponse)
async def complete_commitment(
    commitment_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: CommitmentManager = Depends(get_commitment_manager_dependency),
):
    """
    Dedicated lifecycle endpoint to mark a commitment completed.
    Does NOT soft delete the item (is_active remains True).
    """
    user_id = principal.user.user_id
    commitment = await manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
    if commitment is None or not commitment.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active commitment '{commitment_id}' not found.",
        )
    await ensure_commitments_permission(user_id, permission_manager)

    try:
        updated = await manager.complete_commitment(commitment_id=commitment_id, user_id=user_id)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Commitment '{commitment_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/{commitment_id}/cancel", response_model=CommitmentResponse)
async def cancel_commitment(
    commitment_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: CommitmentManager = Depends(get_commitment_manager_dependency),
):
    """
    Dedicated lifecycle endpoint to mark a commitment cancelled.
    Does NOT soft delete the item (is_active remains True).
    """
    user_id = principal.user.user_id
    commitment = await manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
    if commitment is None or not commitment.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active commitment '{commitment_id}' not found.",
        )
    await ensure_commitments_permission(user_id, permission_manager)

    try:
        updated = await manager.cancel_commitment(commitment_id=commitment_id, user_id=user_id)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Commitment '{commitment_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/{commitment_id}", response_model=CommitmentDeleteResponse)
async def delete_commitment(
    commitment_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    permission_manager: PermissionManager = Depends(get_permission_manager_dependency),
    manager: CommitmentManager = Depends(get_commitment_manager_dependency),
):
    """
    Safe soft deletion (is_active = False).
    Does NOT mark the commitment completed or cancelled.
    """
    user_id = principal.user.user_id
    existing = await manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
    if existing is None or not existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active commitment '{commitment_id}' not found.",
        )
    await ensure_commitments_permission(user_id, permission_manager)

    success = await manager.delete_commitment(commitment_id=commitment_id, user_id=user_id, soft=True)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Failed to delete commitment '{commitment_id}'.",
        )

    return CommitmentDeleteResponse(
        id=commitment_id,
        status="deleted",
        deleted=True,
    )
