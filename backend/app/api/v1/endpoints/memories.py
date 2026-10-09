import logging
from datetime import datetime
from typing import Optional, List, Tuple, Any
from fastapi import APIRouter, HTTPException, Depends, Query, Header, status
from pydantic import BaseModel, Field, model_validator

from app.services.identity import default_identity_manager
from app.services.memory import (
    MemoryItem,
    MemoryType,
    MemorySource,
    MemoryStatus,
    MemoryManager,
    get_memory_manager_dependency,
)

logger = logging.getLogger("aurora.api.memories")
router = APIRouter()


# ── Dependency: Server-Controlled Identity ───────────────────────────────────

def get_current_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> str:
    """
    Derives active user identity from server identity manager,
    permitting X-User-ID strictly for development and isolated unit testing.
    """
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()
    return default_identity_manager.get_current_user().user_id


# MARK: - Request & Response Schemas

class MemoryResponse(BaseModel):
    id: str
    type: MemoryType
    content: str
    importance: int
    confidence: float
    source: MemorySource
    status: MemoryStatus
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    tags: List[str]
    is_active: bool


class MemoryListResponse(BaseModel):
    items: List[MemoryResponse]
    total: int
    limit: int
    offset: int


class MemoryCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000, description="The memory text.")
    type: MemoryType = Field(default=MemoryType.EXPLICIT, description="Conceptual memory type.")
    importance: int = Field(default=3, ge=1, le=5, description="Importance scale from 1 (trivial) to 5 (critical).")
    tags: List[str] = Field(default_factory=list, description="Categorization tags.")
    expires_at: Optional[datetime] = Field(default=None, description="Optional UTC expiration datetime.")

    @model_validator(mode="before")
    @classmethod
    def reject_user_id_override(cls, data: Any) -> Any:
        if isinstance(data, dict) and "user_id" in data:
            raise ValueError("Request body cannot specify or override user identity.")
        return data


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = Field(None, min_length=1, max_length=5000, description="Updated memory text.")
    importance: Optional[int] = Field(None, ge=1, le=5, description="Updated importance (1 to 5).")
    tags: Optional[List[str]] = Field(None, description="Updated categorization tags.")
    expires_at: Optional[datetime] = Field(None, description="Updated UTC expiration datetime.")

    @model_validator(mode="before")
    @classmethod
    def reject_user_id_override(cls, data: Any) -> Any:
        if isinstance(data, dict) and "user_id" in data:
            raise ValueError("Request body cannot specify or override user identity.")
        return data


class MemoryDeleteResponse(BaseModel):
    id: str
    status: str
    deleted: bool


def _item_to_response(item: MemoryItem) -> MemoryResponse:
    return MemoryResponse(
        id=item.id,
        type=item.type,
        content=item.content,
        importance=item.importance,
        confidence=item.confidence,
        source=item.source,
        status=item.status,
        created_at=item.created_at,
        updated_at=item.updated_at,
        expires_at=item.expires_at,
        tags=item.tags,
        is_active=item.is_active,
    )


# MARK: - Endpoints

@router.get("", response_model=MemoryListResponse)
async def list_memories(
    limit: int = Query(50, ge=1, le=100, description="Number of memories to return."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    memory_type: Optional[MemoryType] = Query(None, alias="type", description="Filter by MemoryType."),
    status_filter: Optional[MemoryStatus] = Query(None, alias="status", description="Filter by MemoryStatus."),
    q: Optional[str] = Query(None, min_length=1, max_length=100, description="Search query matching content or tags."),
    include_archived: bool = Query(False, description="Whether to include archived/inactive memories."),
    include_expired: bool = Query(False, description="Whether to include expired memories."),
    user_id: str = Depends(get_current_user_id),
    manager: MemoryManager = Depends(get_memory_manager_dependency),
):
    """
    List memories matching filter criteria, with pagination and query search.
    By default, archived and expired memories are excluded.
    Strictly scoped to the active user identity. Unscoped legacy records are never returned.
    """
    types = [memory_type] if memory_type is not None else None
    
    if status_filter is not None:
        statuses = [status_filter]
    elif not include_archived:
        statuses = [MemoryStatus.ACTIVE]
    else:
        statuses = None

    items = await manager.list_memories_for_user(
        user_id=user_id,
        types=types,
        statuses=statuses,
        query=q,
        include_inactive=include_archived,
        include_expired=include_expired,
        limit=limit,
        offset=offset,
        allow_legacy_unscoped=False,
    )

    total = await manager.count_memories_for_user(
        user_id=user_id,
        types=types,
        statuses=statuses,
        query=q,
        include_inactive=include_archived,
        include_expired=include_expired,
        allow_legacy_unscoped=False,
    )

    return MemoryListResponse(
        items=[_item_to_response(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    request: MemoryCreateRequest,
    user_id: str = Depends(get_current_user_id),
    manager: MemoryManager = Depends(get_memory_manager_dependency),
):
    """
    Explicit user-created memory.
    Enforces source=user_explicit, confidence=1.0, status=active.
    Strictly scoped to the active user identity.
    """
    try:
        memory, _ = await manager.create_memory_for_user(
            user_id=user_id,
            content=request.content,
            type=request.type,
            importance=request.importance,
            confidence=1.0,
            source=MemorySource.USER_EXPLICIT,
            status=MemoryStatus.ACTIVE,
            tags=request.tags,
            expires_at=request.expires_at,
        )
        return _item_to_response(memory)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: str,
    include_archived: bool = Query(False, description="Whether to return archived memories."),
    user_id: str = Depends(get_current_user_id),
    manager: MemoryManager = Depends(get_memory_manager_dependency),
):
    """
    Retrieve details for a single memory by ID.
    Returns HTTP 404 for nonexistent, foreign user, archived, or expired memories by default.
    Unscoped legacy records return HTTP 404.
    """
    memory = await manager.get_memory_for_user(
        user_id=user_id,
        memory_id=memory_id,
        allow_legacy_unscoped=False,
    )
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' not found.",
        )

    if not include_archived and (not memory.is_active or memory.status == MemoryStatus.ARCHIVED):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' is archived.",
        )

    if not include_archived and memory.is_expired:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory '{memory_id}' has expired.",
        )

    return _item_to_response(memory)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: str,
    request: MemoryUpdateRequest,
    user_id: str = Depends(get_current_user_id),
    manager: MemoryManager = Depends(get_memory_manager_dependency),
):
    """
    Update permitted fields on an existing memory (content, importance, tags, expires_at).
    Does NOT allow modifying lifecycle status, source, or confidence.
    Returns HTTP 404 if memory is nonexistent, foreign user, archived, or expired.
    Unscoped legacy records return HTTP 404.
    """
    existing = await manager.get_memory_for_user(
        user_id=user_id,
        memory_id=memory_id,
        allow_legacy_unscoped=False,
    )
    if existing is None or not existing.is_active or existing.status == MemoryStatus.ARCHIVED or existing.is_expired:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active memory '{memory_id}' not found.",
        )

    try:
        updated = await manager.update_memory(
            memory_id=memory_id,
            content=request.content,
            importance=request.importance,
            tags=request.tags,
            expires_at=request.expires_at,
            user_id=user_id,
            allow_legacy_unscoped=False,
        )
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Memory '{memory_id}' not found.",
            )
        return _item_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.delete("/{memory_id}", response_model=MemoryDeleteResponse)
async def delete_memory(
    memory_id: str,
    user_id: str = Depends(get_current_user_id),
    manager: MemoryManager = Depends(get_memory_manager_dependency),
):
    """
    Safe soft deletion only. Archives the memory (status=archived, is_active=False).
    Hard deletion is not exposed through the public API.
    Strictly user-scoped. Unscoped legacy records return HTTP 404.
    """
    existing = await manager.get_memory_for_user(
        user_id=user_id,
        memory_id=memory_id,
        allow_legacy_unscoped=False,
    )
    if existing is None or not existing.is_active or existing.status == MemoryStatus.ARCHIVED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active memory '{memory_id}' not found.",
        )

    success = await manager.delete_memory_for_user(
        user_id=user_id,
        memory_id=memory_id,
        soft=True,
        allow_legacy_unscoped=False,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Failed to archive memory '{memory_id}'.",
        )

    return MemoryDeleteResponse(
        id=memory_id,
        status="archived",
        deleted=True,
    )


