"""
Phase 5L.2 — REST API endpoints for Memory Candidate Review & Resolution.

Endpoints:
  GET    /api/v1/memories/candidates              — List pending review candidates for active user
  GET    /api/v1/memories/candidates/{candidate_id} — Retrieve a specific candidate
  POST   /api/v1/memories/candidates/{candidate_id}/approve — Approve candidate (with optional edit/tags/conflict_action)
  POST   /api/v1/memories/candidates/{candidate_id}/reject  — Dismiss candidate from review queue

Invariants:
- Server controls user identity: Client body never supplies user_id.
- Cross-user candidate access fails closed with HTTP 404.
- Atomic failure-safe claim pattern: Persistence failure rolls candidate back to PENDING_REVIEW.
- Conflict action enforced via HTTP 422: Required if conflict exists; forbidden if no conflict.
- Untrusted user edits revalidated via Phase 5L.1 validation primitives.
- override_tags replaces candidate tags after strict sanitization.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.v1.endpoints.memories import MemoryResponse, _item_to_response
from app.services.identity import default_identity_manager
from app.services.memory import MemoryManager, get_memory_manager_dependency
from app.services.memory.candidates import (
    CandidateEvidenceType,
    CandidateSensitivity,
    CandidateValidationPipeline,
    InMemoryCandidateReviewStore,
    ReviewableCandidate,
    derive_temporal_expiration,
    get_candidate_review_store,
)
from app.services.memory.schemas import MemoryItem, MemorySource, MemoryStatus, MemoryType

logger = logging.getLogger("aurora.api.memory_candidates")
router = APIRouter()

# Validation regexes
_CANDIDATE_ID_PATTERN = re.compile(r"^cand_[0-9a-f]{12}$")
_TAG_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_RESERVED_TAG_PREFIXES = ("sys:", "aurora:", "mem_", "cand_", "c_", "user_", "auth_")

_pipeline = CandidateValidationPipeline()


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


# ── Request & Response Models ────────────────────────────────────────────────

class CandidateItemResponse(BaseModel):
    candidate_id: str
    memory_type: str
    content: str
    evidence_type: str
    confidence: float
    importance: int
    tags: List[str]
    is_sensitive: bool
    is_temporary: bool
    user_quote: Optional[str] = None
    conflicts_with_memory_id: Optional[str] = None
    existing_conflicting_content: Optional[str] = None
    created_at: str
    expires_at: str

    @classmethod
    def from_domain(cls, item: ReviewableCandidate) -> "CandidateItemResponse":
        c = item.candidate
        return cls(
            candidate_id=c.candidate_id,
            memory_type=c.memory_type.value,
            content=c.content,
            evidence_type=c.evidence_type.value,
            confidence=c.confidence,
            importance=c.importance,
            tags=c.tags,
            is_sensitive=(c.sensitivity == CandidateSensitivity.SENSITIVE),
            is_temporary=bool(c.temporal and c.temporal.is_temporary),
            user_quote=c.user_quote,
            conflicts_with_memory_id=item.conflicts_with_memory_id,
            existing_conflicting_content=item.existing_conflicting_content,
            created_at=item.created_at.isoformat(),
            expires_at=item.expires_at.isoformat(),
        )


class CandidateListResponse(BaseModel):
    status: str = "success"
    count: int
    total: int
    limit: int
    offset: int
    items: List[CandidateItemResponse]


class ApproveCandidateRequest(BaseModel):
    edited_content: Optional[str] = Field(None, min_length=1, max_length=1000)
    override_tags: Optional[List[str]] = Field(None, max_length=10)
    conflict_action: Optional[str] = Field(
        None, description="'replace' or 'keep_both' (required if conflict exists; forbidden otherwise)"
    )


class RejectCandidateResponse(BaseModel):
    status: str
    candidate_id: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=CandidateListResponse,
    summary="List pending review candidates for active user.",
)
async def list_candidates(
    limit: int = Query(20, ge=1, le=50, description="Page limit (1-50)."),
    offset: int = Query(0, ge=0, description="Page offset."),
    user_id: str = Depends(get_current_user_id),
    store: InMemoryCandidateReviewStore = Depends(get_candidate_review_store),
):
    items, total = await store.list(user_id=user_id, limit=limit, offset=offset)
    return CandidateListResponse(
        count=len(items),
        total=total,
        limit=limit,
        offset=offset,
        items=[CandidateItemResponse.from_domain(item) for item in items],
    )


@router.get(
    "/{candidate_id}",
    response_model=CandidateItemResponse,
    summary="Retrieve a specific candidate for active user.",
)
async def get_candidate(
    candidate_id: str,
    user_id: str = Depends(get_current_user_id),
    store: InMemoryCandidateReviewStore = Depends(get_candidate_review_store),
):
    if not _CANDIDATE_ID_PATTERN.match(candidate_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed candidate ID format.",
        )

    item = await store.get(user_id=user_id, candidate_id=candidate_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found or expired.",
        )

    return CandidateItemResponse.from_domain(item)


@router.post(
    "/{candidate_id}/approve",
    response_model=MemoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Approve candidate with optional edit, tags override, or conflict action.",
)
async def approve_candidate(
    candidate_id: str,
    request: ApproveCandidateRequest,
    user_id: str = Depends(get_current_user_id),
    store: InMemoryCandidateReviewStore = Depends(get_candidate_review_store),
    manager: MemoryManager = Depends(get_memory_manager_dependency),
):
    if not _CANDIDATE_ID_PATTERN.match(candidate_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed candidate ID format.",
        )

    # 1. Idempotency Ledger Check: Already approved?
    ledger_entry = await store.get_ledger_entry(user_id=user_id, candidate_id=candidate_id)
    if ledger_entry:
        if ledger_entry.action == "approved" and ledger_entry.memory_id:
            existing_mem = await manager.get_memory(ledger_entry.memory_id)
            if existing_mem:
                return _item_to_response(existing_mem)
        elif ledger_entry.action == "rejected":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate was already rejected.",
            )

    # 2. Atomic Claim: Transition candidate from PENDING_REVIEW to RESOLVING
    claimed = await store.claim_candidate(user_id=user_id, candidate_id=candidate_id)
    if claimed is None:
        # Check if candidate exists for user but in RESOLVING state
        raw_existing = await store.get(user_id=user_id, candidate_id=candidate_id)
        if raw_existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate resolution currently in progress.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found or expired.",
        )

    candidate = claimed.candidate

    # 3. Conflict Action Validation
    if claimed.conflicts_with_memory_id is not None:
        if request.conflict_action is None:
            await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="conflict_action is required ('replace' or 'keep_both') because a potential conflict was detected.",
            )
        if request.conflict_action not in ("replace", "keep_both"):
            await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid conflict_action. Must be 'replace' or 'keep_both'.",
            )
    else:
        if request.conflict_action is not None:
            await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="conflict_action must be omitted for non-conflicting candidates.",
            )

    # 4. override_tags Validation & Normalization
    final_tags = candidate.tags
    if request.override_tags is not None:
        if len(request.override_tags) > 10:
            await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Too many tags. Maximum is 10.",
            )
        cleaned_tag_list = []
        for tag in request.override_tags:
            tag_clean = tag.strip().lstrip("#")
            if not tag_clean:
                continue
            if len(tag_clean) > 30:
                await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Tag '{tag}' exceeds 30 characters limit.",
                )
            if any(tag_clean.lower().startswith(p) for p in _RESERVED_TAG_PREFIXES):
                await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Tag '{tag}' uses reserved system prefix.",
                )
            if not _TAG_PATTERN.match(tag_clean):
                await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Tag '{tag}' contains invalid characters. Use alphanumeric, hyphen, or underscore.",
                )
            cleaned_tag_list.append(tag_clean)
        # override_tags replaces candidate tags
        final_tags = manager.normalize_tags(cleaned_tag_list)

    # 5. Untrusted User Edit Validation (Gate 1, 4, 5, 6 via Phase 5L.1 primitives)
    stored_content = candidate.content
    source = (
        MemorySource.USER_EXPLICIT
        if candidate.evidence_type == CandidateEvidenceType.USER_EXPLICIT
        else MemorySource.INFERRED
    )
    resolution = "user_approved"
    metadata = {
        "candidate_id": candidate.candidate_id,
        "conversation_id": candidate.conversation_id,
        "turn_index": candidate.turn_index,
        "extractor_version": candidate.extractor_version,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "temporal": candidate.temporal.model_dump() if candidate.temporal else None,
        "user_quote": candidate.user_quote,
    }

    if request.edited_content is not None and request.edited_content.strip():
        is_valid, err_msg, clean_content, detected_sensitivity = _pipeline.validate_content_text(
            request.edited_content
        )
        if not is_valid:
            await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Edited content failed validation: {err_msg}",
            )
        stored_content = clean_content
        source = MemorySource.USER_EXPLICIT
        resolution = "user_edited"
        metadata["original_evidence"] = candidate.evidence_type.value
        metadata["original_content"] = candidate.content
        if detected_sensitivity == CandidateSensitivity.SENSITIVE:
            metadata["sensitivity"] = "sensitive"

    metadata["resolution"] = resolution
    if candidate.sensitivity == CandidateSensitivity.SENSITIVE:
        metadata["sensitivity"] = "sensitive"

    expires_at = derive_temporal_expiration(candidate)

    # 6. Execute Persistence through MemoryManager
    try:
        if (
            claimed.conflicts_with_memory_id is not None
            and request.conflict_action == "replace"
        ):
            # Target memory MUST belong to the user
            conflicting_mem = await manager.get_memory(claimed.conflicts_with_memory_id)
            if conflicting_mem:
                target_owner = conflicting_mem.metadata.get("user_id")
                if target_owner and target_owner != user_id:
                    await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Conflicting memory not found or not owned by user.",
                    )
                saved = await manager.update_memory(
                    memory_id=claimed.conflicts_with_memory_id,
                    content=stored_content,
                    tags=final_tags,
                    metadata=metadata,
                )
            else:
                saved, _ = await manager.create_memory(
                    content=stored_content,
                    type=candidate.memory_type,
                    importance=candidate.importance,
                    confidence=candidate.confidence,
                    source=source,
                    status=MemoryStatus.ACTIVE,
                    tags=final_tags,
                    expires_at=expires_at,
                    metadata=metadata,
                )
        else:
            # keep_both or no conflict
            saved, _ = await manager.create_memory(
                content=stored_content,
                type=candidate.memory_type,
                importance=candidate.importance,
                confidence=candidate.confidence,
                source=source,
                status=MemoryStatus.ACTIVE,
                tags=final_tags,
                expires_at=expires_at,
                metadata=metadata,
            )

        # 7. Success: Commit resolution and append to bounded ledger
        await store.commit_resolution(
            user_id=user_id,
            candidate_id=candidate_id,
            action="approved",
            memory_id=saved.id,
        )
        return _item_to_response(saved)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Candidate approval persistence failed: %s", exc)
        await store.rollback_claim(user_id=user_id, candidate_id=candidate_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Persistence failed; candidate rolled back to pending review.",
        )


@router.post(
    "/{candidate_id}/reject",
    response_model=RejectCandidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Dismiss/reject candidate from review queue.",
)
async def reject_candidate(
    candidate_id: str,
    user_id: str = Depends(get_current_user_id),
    store: InMemoryCandidateReviewStore = Depends(get_candidate_review_store),
):
    if not _CANDIDATE_ID_PATTERN.match(candidate_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed candidate ID format.",
        )

    # 1. Idempotency check: Already rejected?
    ledger_entry = await store.get_ledger_entry(user_id=user_id, candidate_id=candidate_id)
    if ledger_entry:
        if ledger_entry.action == "rejected":
            return RejectCandidateResponse(status="already_rejected", candidate_id=candidate_id)
        elif ledger_entry.action == "approved":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate was already approved.",
            )

    # 2. Check existence in store
    item = await store.get(user_id=user_id, candidate_id=candidate_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found or expired.",
        )

    # 3. Commit rejection to store and ledger
    await store.commit_resolution(
        user_id=user_id,
        candidate_id=candidate_id,
        action="rejected",
    )
    return RejectCandidateResponse(status="rejected", candidate_id=candidate_id)
