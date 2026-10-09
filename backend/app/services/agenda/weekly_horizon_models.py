from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.commitments.models import Commitment, CommitmentStatus
from app.services.tasks.models import Task, TaskPriority, TaskStatus
from app.services.memory.schemas import MemorySource, MemoryStatus, MemoryType
from app.services.reminders.models import Reminder
from app.services.permissions.models import PermissionType

# ── Authoritative Proposal TTL Contract (Phase 6H) ───────────────────────────
WEEKLY_PROPOSAL_TTL_MINUTES: int = 30
WEEKLY_PROPOSAL_TTL_SECONDS: int = WEEKLY_PROPOSAL_TTL_MINUTES * 60


class CaseInsensitiveEnum(str, Enum):
    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            for member in cls:
                if member.value.lower() == value.lower() or member.name.lower() == value.lower():
                    return member
        return None


class WeeklyHorizonCongestionLevel(CaseInsensitiveEnum):
    LIGHT = "light"
    BALANCED = "balanced"
    CONGESTED = "congested"


class WeeklyProposalType(CaseInsensitiveEnum):
    WEEKLY_PLAN = "weekly_plan"
    WEEKLY_RETROSPECTIVE = "weekly_retrospective"


class WeeklyProposalStatus(CaseInsensitiveEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    PARTIALLY_EXECUTED = "partially_executed"
    FAILED = "failed"
    ABORTED = "aborted"
    EXPIRED = "expired"


class WeeklyFailureReason(CaseInsensitiveEnum):
    EXPIRED = "EXPIRED"
    INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS"
    STATE_HASH_MISMATCH = "STATE_HASH_MISMATCH"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    OPERATION_FAILED = "OPERATION_FAILED"
    SCHEDULED_TIME_IN_PAST = "SCHEDULED_TIME_IN_PAST"
    CALENDAR_SLOT_COLLISION = "CALENDAR_SLOT_COLLISION"
    WORKING_HOURS_MISMATCH = "WORKING_HOURS_MISMATCH"
    PERMISSION_REVOKED = "PERMISSION_REVOKED"


class ReminderLinkType(CaseInsensitiveEnum):
    NONE = "none"
    TASK = "task"
    COMMITMENT = "commitment"


class WeeklyOperationType(CaseInsensitiveEnum):
    ALLOCATE_TASK_DUE_DATE = "allocate_task_due_date"
    CREATE_MILESTONE_REMINDER = "create_milestone_reminder"
    CREATE_STRATEGIC_MEMORY = "create_strategic_memory"
    COMPLETE_HONORED_COMMITMENT = "complete_honored_commitment"


class WeeklyOperationStatus(CaseInsensitiveEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WeeklyStateTransitionError(RuntimeError):
    """Raised when an illegal weekly horizon proposal state transition is attempted."""
    pass


class PreconditionFailedError(RuntimeError):
    """Raised when an operation execution precondition or state check fails."""
    pass


# ── Canonical Fingerprinting Algorithm ───────────────────────────────────────

def compute_intra_proposal_fingerprint(payload: Dict[str, Any]) -> str:
    """
    Computes deterministic SHA-256 hex digest for an intra-proposal dependency payload.
    Uses canonical JSON sorting, strict separators, and ASCII encoding.
    """
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def extract_canonical_task_payload(task: Task) -> Dict[str, Any]:
    """
    Extracts the exhaustive authoritative field set participating in Task post-state fingerprinting.
    Any external change to ANY of these fields alters the fingerprint.
    """
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status.value if isinstance(task.status, Enum) else str(task.status),
        "priority": task.priority.value if isinstance(task.priority, Enum) else str(task.priority),
        "due_at": task.due_at.astimezone(timezone.utc).isoformat() if task.due_at else None,
        "tags": sorted(task.tags or []),
        "source_commitment_id": task.source_commitment_id,
        "updated_at": task.updated_at.astimezone(timezone.utc).isoformat() if task.updated_at else None,
        "user_id": task.user_id,
    }


# ── Preconditions ─────────────────────────────────────────────────────────────

class TaskAllocatePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    expected_status: TaskStatus = Field(default=TaskStatus.PENDING)
    expected_due_at: Optional[datetime] = None
    expected_source_commitment_id: Optional[str] = None
    target_date: date
    allocated_slot_start: datetime  # Bound UTC start of verified free slot
    allocated_slot_end: datetime    # Bound UTC end of verified free slot
    effective_duration_minutes: int
    parent_commitment_target_date: Optional[date] = None
    effective_work_start_hour: int = Field(ge=0, le=23)  # Bound server-derived working hour
    effective_work_end_hour: int = Field(ge=1, le=24)    # Bound server-derived working hour

    @model_validator(mode="after")
    def validate_allocation_invariants(self) -> "TaskAllocatePrecondition":
        if self.allocated_slot_start.tzinfo is None or self.allocated_slot_end.tzinfo is None:
            raise ValueError("Allocated slot timestamps must be timezone-aware.")
        if self.allocated_slot_start >= self.allocated_slot_end:
            raise ValueError("allocated_slot_start must be strictly earlier than allocated_slot_end.")
        if self.effective_duration_minutes < 15:
            raise ValueError("effective_duration_minutes must be >= 15.")
        if self.effective_work_start_hour >= self.effective_work_end_hour:
            raise ValueError("effective_work_start_hour must be strictly less than effective_work_end_hour.")
        return self


class WeeklyReminderCreatePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_title: str = Field(min_length=1, max_length=500)
    exact_scheduled_at: datetime  # Timezone-aware UTC instant
    linked_entity_type: ReminderLinkType = Field(default=ReminderLinkType.NONE)
    linked_entity_id: Optional[str] = None
    calendar_slot_start: Optional[datetime] = None
    calendar_slot_end: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_reminder_invariants(self) -> "WeeklyReminderCreatePrecondition":
        if self.exact_scheduled_at.tzinfo is None:
            raise ValueError("exact_scheduled_at must be timezone-aware.")
        if self.linked_entity_type == ReminderLinkType.NONE and self.linked_entity_id is not None:
            raise ValueError("linked_entity_id must be None when linked_entity_type is NONE.")
        if self.linked_entity_type != ReminderLinkType.NONE and not self.linked_entity_id:
            raise ValueError(f"linked_entity_id is required when linked_entity_type is {self.linked_entity_type.value}.")
        return self


class WeeklyMemoryCreatePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str = Field(min_length=1, max_length=2000)
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: MemorySource = Field(default=MemorySource.USER_EXPLICIT)
    status: MemoryStatus = Field(default=MemoryStatus.ACTIVE)
    tags: List[str] = Field(default_factory=list)


class WeeklyCommitmentCompletePrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commitment_id: str = Field(min_length=1)
    expected_status: CommitmentStatus = Field(default=CommitmentStatus.ACTIVE)
    linked_task_ids: List[str] = Field(default_factory=list)
    expected_completed_task_ids: List[str] = Field(default_factory=list)


WeeklyOperationPrecondition = Union[
    TaskAllocatePrecondition,
    WeeklyReminderCreatePrecondition,
    WeeklyMemoryCreatePrecondition,
    WeeklyCommitmentCompletePrecondition,
]


# ── Intra-Proposal Post-State Model ──────────────────────────────────────────

class IntraProposalDependencyPostState(BaseModel):
    """
    Server-generated, execution-scoped, in-memory representation of authoritative
    entity state resulting from a successful producer operation in the same proposal.
    NEVER persisted to SQLite; client cannot supply or modify.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str = Field(min_length=1)
    producer_operation_id: str = Field(min_length=1)
    entity_type: str = Field(pattern="^(TASK|COMMITMENT)$")
    entity_id: str = Field(min_length=1)
    captured_at: datetime  # Server UTC instant immediately following authoritative mutation
    canonical_payload: Dict[str, Any]
    post_state_fingerprint: str = Field(min_length=64, max_length=64)  # Hex SHA-256


# ── Operations and Proposals ─────────────────────────────────────────────────

class WeeklyOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1)
    operation_type: WeeklyOperationType
    execution_order: int = Field(ge=0)
    status: WeeklyOperationStatus = Field(default=WeeklyOperationStatus.PENDING)
    required_permissions: List[PermissionType] = Field(default_factory=list)
    precondition: WeeklyOperationPrecondition
    dependency_entities: List[Dict[str, str]] = Field(default_factory=list)  # e.g. [{"type": "task", "id": "..."}]
    dependency_state_snapshot: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_entity_id: Optional[str] = None


class WeeklyPlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    proposal_type: WeeklyProposalType = Field(default=WeeklyProposalType.WEEKLY_PLAN)
    status: WeeklyProposalStatus = Field(default=WeeklyProposalStatus.DRAFT)
    created_at: datetime
    expires_at: datetime
    state_hash: str = Field(min_length=64, max_length=64)
    required_permissions: List[PermissionType] = Field(default_factory=list)
    operations: List[WeeklyOperation] = Field(default_factory=list)
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    post_states: Dict[str, IntraProposalDependencyPostState] = Field(default_factory=dict)


class WeeklyRetrospectiveProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    proposal_type: WeeklyProposalType = Field(default=WeeklyProposalType.WEEKLY_RETROSPECTIVE)
    status: WeeklyProposalStatus = Field(default=WeeklyProposalStatus.DRAFT)
    created_at: datetime
    expires_at: datetime
    state_hash: str = Field(min_length=64, max_length=64)
    required_permissions: List[PermissionType] = Field(default_factory=list)
    operations: List[WeeklyOperation] = Field(default_factory=list)
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    post_states: Dict[str, IntraProposalDependencyPostState] = Field(default_factory=dict)


# ── Domain Read Synthesis Models ─────────────────────────────────────────────

class DayCapacitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_str: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    day_of_week: str
    total_focus_minutes: int
    allocated_focus_minutes: int
    free_focus_minutes: int
    events_count: int
    tasks_count: int
    congestion_level: WeeklyHorizonCongestionLevel
    is_overcommitted: bool = False


class CommitmentHealthItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    commitment_id: str
    title: str
    target_date: Optional[date] = None
    linked_tasks_count: int
    completed_tasks_count: int
    open_tasks_count: int
    is_orphan: bool = False
    has_overrun_risk: bool = False
    overrun_warning: Optional[str] = None


class WeeklyStrategicHorizon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    timezone: str
    horizon_start_date: str
    horizon_end_date: str
    day_summaries: List[DayCapacitySummary] = Field(default_factory=list)
    commitment_health: List[CommitmentHealthItem] = Field(default_factory=list)
    orphan_commitments_count: int = 0
    overcommitted_days_count: int = 0
    total_free_focus_minutes: int = 0
    strategic_recommendations: List[str] = Field(default_factory=list)
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)


class WeeklyRetrospective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    timezone: str
    lookback_start_date: str
    lookback_end_date: str
    completed_tasks_count: int = 0
    completed_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    honored_commitments_count: int = 0
    honored_commitments: List[Dict[str, Any]] = Field(default_factory=list)
    unfulfilled_commitments_count: int = 0
    unfulfilled_commitments: List[Dict[str, Any]] = Field(default_factory=list)
    recurring_rollover_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    reflection_memories: List[Dict[str, Any]] = Field(default_factory=list)
    velocity_score: float = Field(default=0.0, description="Presentation only velocity ratio")
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)


# ── State Transition Validation ──────────────────────────────────────────────

def validate_weekly_state_transition(
    current: WeeklyProposalStatus,
    target: WeeklyProposalStatus,
) -> None:
    """
    Enforces valid state machine transitions:
    DRAFT -> APPROVED | ABORTED
    APPROVED -> EXECUTING | ABORTED
    EXECUTING -> EXECUTED | PARTIALLY_EXECUTED | FAILED
    Terminal states cannot transition.
    """
    allowed_transitions = {
        WeeklyProposalStatus.DRAFT: {WeeklyProposalStatus.APPROVED, WeeklyProposalStatus.ABORTED, WeeklyProposalStatus.EXPIRED},
        WeeklyProposalStatus.APPROVED: {WeeklyProposalStatus.EXECUTING, WeeklyProposalStatus.ABORTED, WeeklyProposalStatus.EXPIRED},
        WeeklyProposalStatus.EXECUTING: {WeeklyProposalStatus.EXECUTED, WeeklyProposalStatus.PARTIALLY_EXECUTED, WeeklyProposalStatus.FAILED},
    }

    if current in {
        WeeklyProposalStatus.EXECUTED,
        WeeklyProposalStatus.PARTIALLY_EXECUTED,
        WeeklyProposalStatus.FAILED,
        WeeklyProposalStatus.ABORTED,
        WeeklyProposalStatus.EXPIRED,
    }:
        raise WeeklyStateTransitionError(f"Proposal is in terminal state '{current.value}' and cannot transition to '{target.value}'.")

    valid_targets = allowed_transitions.get(current, set())
    if target not in valid_targets:
        raise WeeklyStateTransitionError(f"Invalid proposal state transition: cannot transition from '{current.value}' to '{target.value}'.")
