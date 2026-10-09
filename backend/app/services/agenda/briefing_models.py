from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.commitments.models import Commitment
from app.services.tasks.models import Task, TaskPriority
from app.services.memory.schemas import MemoryType

# ── Authoritative Proposal TTL Contract (Phase 6F) ───────────────────────────
DEBRIEF_PROPOSAL_TTL_MINUTES: int = 30
DEBRIEF_PROPOSAL_TTL_SECONDS: int = DEBRIEF_PROPOSAL_TTL_MINUTES * 60


class DebriefProposalStatus(str, Enum):
    """
    Authoritative lifecycle states of a DebriefProposal.
    Strictly enforced finite state machine.
    """
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIALLY_EXECUTED = "partially_executed"
    ABORTED = "aborted"
    EXPIRED = "expired"


class DebriefFailureReason(str, Enum):
    """
    Taxonomy of failure reasons for debrief proposal execution.
    """
    EXPIRED = "EXPIRED"
    INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS"
    STATE_CHANGED = "STATE_CHANGED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    OPERATION_FAILED = "OPERATION_FAILED"
    CALENDAR_NOT_FOUND = "CALENDAR_NOT_FOUND"


class ReminderLinkType(str, Enum):
    """
    Strict typing for reminder linkage.
    Governs mutual exclusivity between task_id and commitment_id.
    """
    NONE = "none"
    TASK = "task"
    COMMITMENT = "commitment"


class DebriefOperationType(str, Enum):
    """
    Governed operations permitted within a debrief proposal.
    """
    CREATE_TASK = "create_task"
    CREATE_REMINDER = "create_reminder"
    CREATE_MEMORY = "create_memory"


class DebriefOperationStatus(str, Enum):
    """
    Per-operation execution status.
    """
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DebriefStateTransitionError(ValueError):
    """Raised when an illegal debrief proposal state transition is attempted."""
    pass


# ── Strict Legal State Transition Table ───────────────────────────────────────
LEGAL_TRANSITIONS: Dict[DebriefProposalStatus, List[DebriefProposalStatus]] = {
    DebriefProposalStatus.DRAFT: [
        DebriefProposalStatus.APPROVED,
        DebriefProposalStatus.ABORTED,
        DebriefProposalStatus.EXPIRED,
    ],
    DebriefProposalStatus.APPROVED: [
        DebriefProposalStatus.EXECUTING,
        DebriefProposalStatus.FAILED,
        DebriefProposalStatus.EXPIRED,
    ],
    DebriefProposalStatus.EXECUTING: [
        DebriefProposalStatus.COMPLETED,
        DebriefProposalStatus.FAILED,
        DebriefProposalStatus.PARTIALLY_EXECUTED,
    ],
    # Terminal states: no transitions allowed
    DebriefProposalStatus.COMPLETED: [],
    DebriefProposalStatus.FAILED: [],
    DebriefProposalStatus.PARTIALLY_EXECUTED: [],
    DebriefProposalStatus.ABORTED: [],
    DebriefProposalStatus.EXPIRED: [],
}


def validate_debrief_state_transition(
    current: DebriefProposalStatus,
    target: DebriefProposalStatus,
) -> None:
    """
    Validates whether transition from `current` to `target` is legal.
    Raises DebriefStateTransitionError if illegal.
    """
    allowed = LEGAL_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise DebriefStateTransitionError(
            f"Illegal debrief proposal state transition: '{current.value}' -> '{target.value}'. "
            f"Allowed transitions from '{current.value}': {[s.value for s in allowed]}."
        )


# ── Typed Operation-Specific Preconditions ────────────────────────────────────

class TaskCreateExecutionPrecondition(BaseModel):
    """
    Immutable execution precondition for creating a task.
    Enforces Phase 6E duration bounds: >= 15 and <= 1440 minutes.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_title: str = Field(min_length=1, max_length=500)
    exact_due_at: Optional[datetime] = None  # INSTANT (UTC)
    exact_duration_minutes: Optional[int] = Field(
        default=None,
        ge=15,
        le=1440,
        description="Optional duration in minutes (>= 15 and <= 1440). If absent, resolves to Phase 6E default (30m)."
    )
    exact_priority: TaskPriority = Field(default=TaskPriority.MEDIUM)
    exact_commitment_id: Optional[str] = None


class ReminderCreateExecutionPrecondition(BaseModel):
    """
    Immutable execution precondition for creating a scheduled reminder.
    Enforces strict typing and mutual exclusivity for linked entities.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_title: str = Field(min_length=1, max_length=500)
    exact_scheduled_at: datetime  # INSTANT (UTC)
    linked_entity_type: ReminderLinkType = Field(default=ReminderLinkType.NONE)
    linked_entity_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_linkage(self) -> "ReminderCreateExecutionPrecondition":
        if self.linked_entity_type == ReminderLinkType.NONE:
            if self.linked_entity_id is not None:
                raise ValueError("linked_entity_id must be None when linked_entity_type is NONE.")
        elif self.linked_entity_type in (ReminderLinkType.TASK, ReminderLinkType.COMMITMENT):
            if not self.linked_entity_id or not self.linked_entity_id.strip():
                raise ValueError(
                    f"linked_entity_id is required when linked_entity_type is {self.linked_entity_type.value}."
                )
        return self


class MemoryCreateExecutionPrecondition(BaseModel):
    """
    Immutable execution precondition for storing an episodic meeting memory.
    NOTE: exact_source is intentionally excluded to guarantee server-authoritative provenance.
    Extra fields are strictly forbidden.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_content: str = Field(min_length=1, max_length=5000)
    exact_memory_type: MemoryType = Field(default=MemoryType.EPISODIC)
    exact_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_event_id: str = Field(min_length=1)
    context_tags: List[str] = Field(default_factory=list)


class DebriefOperationPrecondition(BaseModel):
    """
    Discriminated execution precondition ensuring mutual exclusivity across operations.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_type: DebriefOperationType
    task_create: Optional[TaskCreateExecutionPrecondition] = None
    reminder_create: Optional[ReminderCreateExecutionPrecondition] = None
    memory_create: Optional[MemoryCreateExecutionPrecondition] = None

    @model_validator(mode="after")
    def validate_discriminated_type(self) -> "DebriefOperationPrecondition":
        if self.operation_type == DebriefOperationType.CREATE_TASK:
            if self.task_create is None or self.reminder_create is not None or self.memory_create is not None:
                raise ValueError("CREATE_TASK requires task_create and no other precondition.")
        elif self.operation_type == DebriefOperationType.CREATE_REMINDER:
            if self.reminder_create is None or self.task_create is not None or self.memory_create is not None:
                raise ValueError("CREATE_REMINDER requires reminder_create and no other precondition.")
        elif self.operation_type == DebriefOperationType.CREATE_MEMORY:
            if self.memory_create is None or self.task_create is not None or self.reminder_create is not None:
                raise ValueError("CREATE_MEMORY requires memory_create and no other precondition.")
        return self


class DebriefOperation(BaseModel):
    """
    Single discrete operation within a debrief proposal.
    `proposed_payload` is presentation-only; execution derives strictly from `precondition`.
    """
    operation_id: str = Field(default_factory=lambda: f"dop_{uuid.uuid4().hex[:8]}")
    operation_type: DebriefOperationType
    precondition: DebriefOperationPrecondition
    proposed_payload: Dict[str, Any] = Field(default_factory=dict)
    status: DebriefOperationStatus = Field(default=DebriefOperationStatus.PENDING)
    failure_error: Optional[str] = None
    created_entity_id: Optional[str] = None


class DebriefProposal(BaseModel):
    """
    Server-held, governed proposal for post-event debrief operations.
    """
    proposal_id: str = Field(default_factory=lambda: f"dbp_{uuid.uuid4().hex[:10]}")
    event_id: str
    user_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    status: DebriefProposalStatus = Field(default=DebriefProposalStatus.DRAFT)
    state_hash: str
    ordered_operations: List[DebriefOperation] = Field(default_factory=list)
    failure_reason: Optional[DebriefFailureReason] = None
    applied_at: Optional[datetime] = None


class EventBriefing(BaseModel):
    """
    Contextual pre-event intelligence synthesis. Read-only.
    """
    event_id: str
    user_id: str
    title: str
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    attendees: List[Dict[str, Any]] = Field(default_factory=list)
    related_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    active_commitments: List[Dict[str, Any]] = Field(default_factory=list)
    relevant_memories: List[Dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


# ── Canonical State Serialization & Hashing ───────────────────────────────────

def serialize_canonical_state(
    tasks: List[Task],
    commitments: List[Commitment],
) -> str:
    """
    Computes a deterministic canonical JSON string representing referenced state.

    Temporal Rules:
    - Task.due_at (INSTANT): Normalized to UTC ISO-8601 instant string.
    - Commitment.target_date (DATE_TARGET): Extracted strictly as date-only 'YYYY-MM-DD'.
      NO timezone conversion, NO astimezone(), NO fabricated midnight instant.
    - None values remain null.
    """
    serialized_tasks = [
        {
            "id": t.id,
            "status": t.status.value,
            "due_at": t.due_at.astimezone(timezone.utc).isoformat() if t.due_at else None,
        }
        for t in sorted(tasks, key=lambda x: x.id)
    ]

    serialized_commitments = [
        {
            "id": c.id,
            "status": c.status.value,
            "target_date": c.target_date.strftime("%Y-%m-%d") if c.target_date else None,
        }
        for c in sorted(commitments, key=lambda x: x.id)
    ]

    payload = {
        "tasks": serialized_tasks,
        "commitments": serialized_commitments,
    }

    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def compute_debrief_state_hash(
    tasks: List[Task],
    commitments: List[Commitment],
) -> str:
    """Computes SHA-256 hash of the canonical state serialization."""
    canonical_json = serialize_canonical_state(tasks, commitments)
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()
