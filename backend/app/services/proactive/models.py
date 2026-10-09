"""
Phase 5H — Domain models, enums, and constants for Proactive Assistance Foundation.

Core Invariants:
- ProactiveCandidate represents a potential proposal, NOT authorization to execute an action.
- candidate_id, created_at, expires_at are always server-generated.
- user_id is derived from active Aurora identity.
- metadata is INTERNAL ONLY; never exposed to LLM, iOS, or public APIs.
- reference_id is BACKEND-INTERNAL ONLY; used for provenance and deduplication.
  It must NOT appear in public API response models, iOS models, or UI.
- No autonomous execution or background loops.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Standard TTL for a proactive proposal (1 hour).
DEFAULT_PROACTIVE_TTL_SECONDS: int = 3600

# Strict per-user rate limit (rolling 1-hour window).
MAX_PROACTIVE_CANDIDATES_PER_USER_PER_HOUR: int = 5

# Maximum candidates exposed to context provider per query.
MAX_CONTEXT_CANDIDATES: int = 5

# REST API pagination constants.
DEFAULT_PAGE_SIZE: int = 20
MAX_PAGE_SIZE: int = 50

# Allowed sources (subsystems generating candidates).
ALLOWED_PROACTIVE_SOURCES: frozenset[str] = frozenset(
    {"tasks", "commitments", "calendar", "location", "reminders", "system"}
)


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


class ProactiveStatus(str, Enum):
    """
    Explicit lifecycle states for proactive candidates.
    Never conflate SUPPRESSED, DISMISSED, EXPIRED, and CANCELLED.
    Phase 5I: ACCEPTED (resolution started), EXECUTED (action succeeded), FAILED (action failed/declined).
    """
    PENDING = "pending"          # Ingestion / initial evaluation
    ELIGIBLE = "eligible"        # Evaluated, authorized, and ready for user proposal
    SUPPRESSED = "suppressed"    # Blocked by rate limit, deduplication, or lack of permission
    DELIVERED = "delivered"      # Presented to the user (tracking state)
    DISMISSED = "dismissed"      # User explicitly dismissed the proposal
    EXPIRED = "expired"          # Passed its expires_at timestamp
    CANCELLED = "cancelled"      # Cancelled prior to delivery / dismissed by system
    ACCEPTED = "accepted"        # User accepted proposal; execution resolution started
    EXECUTED = "executed"        # Underlying domain action successfully completed
    FAILED = "failed"            # Action execution failed or user rejected confirmation


class ProactiveTrigger(str, Enum):
    """
    Bounded trigger types representing future evaluation sources.
    No background monitoring in Phase 5H/5I.
    """
    TIME = "time"
    TASK_DUE = "task_due"
    COMMITMENT_DUE = "commitment_due"
    CALENDAR_EVENT = "calendar_event"
    LOCATION_CONTEXT = "location_context"


class ProactiveAction(str, Enum):
    """
    Bounded action types for proactive proposals.
    Never generic; no arbitrary tool execution or function calling.
    Phase 5I: NOTIFY is strictly non-executable.
    """
    NOTIFY = "notify"
    OPEN_REMINDER = "open_reminder"
    OPEN_TASK = "open_task"
    OPEN_COMMITMENT = "open_commitment"
    OPEN_CALENDAR_EVENT = "open_calendar_event"
    COMPLETE_TASK = "complete_task"
    COMPLETE_COMMITMENT = "complete_commitment"
    CANCEL_REMINDER = "cancel_reminder"


class ProactivePriority(str, Enum):
    """Priority weights for deterministic ordering."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def weight(self) -> int:
        weights = {
            ProactivePriority.LOW: 1,
            ProactivePriority.MEDIUM: 2,
            ProactivePriority.HIGH: 3,
        }
        return weights[self]


# ──────────────────────────────────────────────────────────────────────────────
# ProactiveCandidate Model
# ──────────────────────────────────────────────────────────────────────────────


class ProactiveCandidate(BaseModel):
    """
    Domain model representing a potential proactive proposal.

    CRITICAL PRIVACY & SECURITY RULES:
    - candidate != authorization; candidate != execution.
    - candidate_id, created_at, expires_at are always server-controlled.
    - user_id is derived from authenticated Aurora identity.
    - metadata is INTERNAL ONLY; never leaked to LLM or public APIs.
    - reference_id is BACKEND-INTERNAL ONLY; omitted from public/iOS models.
    """

    candidate_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Server-generated unique identifier.",
    )
    user_id: str = Field(
        default=DEFAULT_LOCAL_USER_ID,
        description="Active Aurora identity.",
    )
    trigger: ProactiveTrigger = Field(..., description="Trigger category.")
    title: str = Field(..., min_length=1, max_length=255, description="Sanitized candidate title.")
    message: str = Field(..., min_length=1, max_length=2000, description="User-facing candidate message.")
    priority: ProactivePriority = Field(
        default=ProactivePriority.MEDIUM,
        description="Candidate priority level.",
    )
    status: ProactiveStatus = Field(
        default=ProactiveStatus.PENDING,
        description="Current lifecycle status.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Server-generated UTC creation timestamp.",
    )
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(seconds=DEFAULT_PROACTIVE_TTL_SECONDS),
        description="Server-generated UTC expiration timestamp.",
    )
    source: str = Field(..., description="Origin subsystem (must be in ALLOWED_PROACTIVE_SOURCES).")
    action: ProactiveAction = Field(..., description="Action proposed by this candidate.")

    # BACKEND-INTERNAL ONLY: provenance and deduplication tracking
    reference_id: Optional[str] = Field(
        default=None,
        description="Internal entity reference ID for provenance and deduplication. Never exposed to API/iOS/LLM.",
    )
    suppression_reason: Optional[str] = Field(
        default=None,
        description="Explanation if suppressed (e.g. permission_unauthorized, rate_limit_exceeded, deduplicated).",
    )
    # INTERNAL ONLY: debug/context metadata
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Internal backend-only metadata. Never exposed externally.",
    )

    # BACKEND-INTERNAL ONLY: execution parameters and internal state
    action_payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Backend-internal parameters required to execute action. Never exposed to public API/iOS/LLM.",
    )
    pending_confirmation_id: Optional[str] = Field(
        default=None,
        description="Pointer to Phase 3 PendingConfirmation ID when confirmation is required.",
    )
    internal_execution_result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Raw output from tool or domain manager. Backend-internal only.",
    )
    internal_error: Optional[str] = Field(
        default=None,
        description="Raw error details/stack trace. Backend-internal only.",
    )
    safe_message: Optional[str] = Field(
        default=None,
        description="Sanitized user-facing message upon successful execution.",
    )
    safe_error_message: Optional[str] = Field(
        default=None,
        description="Sanitized user-facing error message if execution fails.",
    )
    navigation_target_type: Optional[str] = Field(
        default=None,
        description="Type of navigation target (task, commitment, calendar_event, reminder).",
    )
    navigation_target_id: Optional[str] = Field(
        default=None,
        description="Public-safe opaque entity ID for client navigation.",
    )
    executed_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the underlying action was completed.",
    )

    # ── Validators ──────────────────────────────────────────────────────────

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("title cannot be empty or whitespace.")
        if len(trimmed) > 255:
            raise ValueError("title exceeds maximum length of 255 characters.")
        return trimmed

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("message cannot be empty or whitespace.")
        if len(trimmed) > 2000:
            raise ValueError("message exceeds maximum length of 2000 characters.")
        return trimmed

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        normalised = v.strip().lower()
        if normalised not in ALLOWED_PROACTIVE_SOURCES:
            raise ValueError(
                f"source '{v}' is not allowed. Permitted sources: {sorted(ALLOWED_PROACTIVE_SOURCES)}"
            )
        return normalised

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Datetime fields must be timezone-aware (UTC).")
        return v

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        """True when the candidate has reached or passed its expires_at timestamp."""
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def ttl_remaining_seconds(self) -> int:
        """Remaining seconds until expiration; 0 if expired."""
        remaining = int((self.expires_at - datetime.now(timezone.utc)).total_seconds())
        return max(0, remaining)

    @property
    def is_terminal(self) -> bool:
        """Terminal states from which a candidate cannot transition further."""
        return self.status in (
            ProactiveStatus.DISMISSED,
            ProactiveStatus.EXPIRED,
            ProactiveStatus.CANCELLED,
            ProactiveStatus.EXECUTED,
            ProactiveStatus.FAILED,
        )

    @property
    def dedup_key(self) -> tuple[str, str, str, str, str]:
        """
        Deterministic deduplication key:
        (user_id, trigger.value, source, reference_id or title, action.value)
        """
        ref = (self.reference_id or self.title).strip().lower()
        return (
            self.user_id,
            self.trigger.value,
            self.source,
            ref,
            self.action.value,
        )
