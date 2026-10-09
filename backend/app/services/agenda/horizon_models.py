from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.commitments.models import Commitment
from app.services.tasks.models import Task, TaskPriority
from app.services.memory.schemas import MemoryType
from app.services.reminders.models import Reminder

# ── Authoritative Proposal TTL Contract (Phase 6G) ───────────────────────────
HORIZON_PROPOSAL_TTL_MINUTES: int = 30
HORIZON_PROPOSAL_TTL_SECONDS: int = HORIZON_PROPOSAL_TTL_MINUTES * 60


class CaseInsensitiveEnum(str, Enum):
    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            for member in cls:
                if member.value.lower() == value.lower() or member.name.lower() == value.lower():
                    return member
        return None


class HorizonProposalType(CaseInsensitiveEnum):
    """
    Discriminator for Phase 6G executive proposals.
    """
    DAY_PLAN = "day_plan"
    EVENING_REVIEW = "evening_review"


class HorizonProposalStatus(CaseInsensitiveEnum):
    """
    Authoritative lifecycle states of a Horizon proposal.
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


class HorizonFailureReason(CaseInsensitiveEnum):
    """
    Taxonomy of failure reasons for horizon proposal execution.
    """
    EXPIRED = "EXPIRED"
    INSUFFICIENT_PERMISSIONS = "INSUFFICIENT_PERMISSIONS"
    STATE_CHANGED = "STATE_CHANGED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    OPERATION_FAILED = "OPERATION_FAILED"
    SCHEDULED_TIME_IN_PAST = "SCHEDULED_TIME_IN_PAST"
    STALE_CALENDAR_STATE = "STALE_CALENDAR_STATE"


class ReminderLinkType(CaseInsensitiveEnum):
    """
    Strict typing for reminder linkage (Phase 6F/6G).
    Governs mutual exclusivity between task_id and commitment_id.
    """
    NONE = "none"
    TASK = "task"
    COMMITMENT = "commitment"


class HorizonOperationType(CaseInsensitiveEnum):
    """
    Governed operations permitted within a horizon proposal.
    """
    ROLLOVER_TASK = "rollover_task"
    CREATE_REMINDER = "create_reminder"
    CREATE_MEMORY = "create_memory"


class HorizonOperationStatus(CaseInsensitiveEnum):
    """
    Per-operation execution status.
    """
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class HorizonStateTransitionError(RuntimeError):
    """Raised when an illegal horizon proposal state transition is attempted."""
    pass


class PreconditionFailedError(RuntimeError):
    """Raised when an execution precondition fails during runtime preflight."""

    def __init__(self, code: HorizonFailureReason, message: Optional[str] = None):
        if not isinstance(code, HorizonFailureReason):
            raise TypeError(f"code must be an instance of HorizonFailureReason, got {type(code).__name__}")
        super().__init__(message or code.value)
        self.code: HorizonFailureReason = code
        self.message: str = message or code.value


CANONICAL_TRANSPORT_CODE_MAP: Dict[HorizonFailureReason, str] = {
    HorizonFailureReason.EXPIRED: "EXPIRED",
    HorizonFailureReason.INSUFFICIENT_PERMISSIONS: "INSUFFICIENT_PERMISSIONS",
    HorizonFailureReason.STATE_CHANGED: "STATE_HASH_MISMATCH",
    HorizonFailureReason.PRECONDITION_FAILED: "PRECONDITION_FAILED",
    HorizonFailureReason.OPERATION_FAILED: "OPERATION_FAILED",
    HorizonFailureReason.SCHEDULED_TIME_IN_PAST: "PRECONDITION_FAILED",
    HorizonFailureReason.STALE_CALENDAR_STATE: "STALE_CALENDAR_STATE",
}


def _validate_transport_code_map() -> None:
    expected_reasons = set(HorizonFailureReason)
    mapped_reasons = set(CANONICAL_TRANSPORT_CODE_MAP.keys())

    missing = expected_reasons - mapped_reasons
    extra = mapped_reasons - expected_reasons

    if missing or extra:
        errors = []
        if missing:
            errors.append(f"Missing mappings for: {[m.value if hasattr(m, 'value') else str(m) for m in sorted(missing, key=lambda x: str(x))]}")
        if extra:
            errors.append(f"Extraneous mappings for: {[e.value if hasattr(e, 'value') else str(e) for e in sorted(extra, key=lambda x: str(x))]}")
        raise RuntimeError(
            f"CANONICAL_TRANSPORT_CODE_MAP initialization failure: {'; '.join(errors)}"
        )


_validate_transport_code_map()


# ── Authoritative Proposal State Machine (Phase 6G) ───────────────────────────
# Legal Transitions:
# DRAFT -> APPROVED | ABORTED | EXPIRED
# APPROVED -> EXECUTING | FAILED | EXPIRED
# EXECUTING -> COMPLETED | FAILED | PARTIALLY_EXECUTED
LEGAL_TRANSITIONS: Dict[HorizonProposalStatus, List[HorizonProposalStatus]] = {
    HorizonProposalStatus.DRAFT: [
        HorizonProposalStatus.APPROVED,
        HorizonProposalStatus.ABORTED,
        HorizonProposalStatus.EXPIRED,
    ],
    HorizonProposalStatus.APPROVED: [
        HorizonProposalStatus.EXECUTING,
        HorizonProposalStatus.FAILED,
        HorizonProposalStatus.EXPIRED,
    ],
    HorizonProposalStatus.EXECUTING: [
        HorizonProposalStatus.COMPLETED,
        HorizonProposalStatus.FAILED,
        HorizonProposalStatus.PARTIALLY_EXECUTED,
    ],
    # Terminal states
    HorizonProposalStatus.COMPLETED: [],
    HorizonProposalStatus.FAILED: [],
    HorizonProposalStatus.PARTIALLY_EXECUTED: [],
    HorizonProposalStatus.ABORTED: [],
    HorizonProposalStatus.EXPIRED: [],
}


def validate_horizon_state_transition(
    current: HorizonProposalStatus,
    target: HorizonProposalStatus,
) -> bool:
    """
    Validates whether transition from `current` to `target` is legal.
    Raises HorizonStateTransitionError if illegal. Returns True if legal.
    """
    allowed = LEGAL_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise HorizonStateTransitionError(
            f"Illegal horizon proposal state transition: '{current.value}' -> '{target.value}'. "
            f"Allowed transitions from '{current.value}': {[s.value for s in allowed]}."
        )
    return True


# ── Presentation-Only Focus Recommendation (Option A) ─────────────────────────

class DayPlanFocusRecommendation(BaseModel):
    """
    Presentation-only recommendation pairing an existing task with an open schedule slot.
    Non-mutating. Zero calendar events created.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    task_title: Optional[str] = "Focus Task"
    slot_start: Optional[datetime] = None  # UTC ISO-8601 instant
    slot_end: Optional[datetime] = None    # UTC ISO-8601 instant
    duration_minutes: Optional[int] = None
    recommended_slot_start: Optional[str] = None
    recommended_slot_end: Optional[str] = None
    rationale: Optional[str] = None
    linked_commitment_id: Optional[str] = None


# ── Typed Operation-Specific Preconditions ────────────────────────────────────

class TaskRolloverPrecondition(BaseModel):
    """
    Immutable execution precondition for rolling over an overdue task.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    exact_new_due_at: datetime  # Bound UTC instant
    exact_new_priority: Optional[TaskPriority] = None


class ReminderCreateExecutionPrecondition(BaseModel):
    """
    Immutable execution precondition for creating a scheduled reminder.
    Pydantic model validation ONLY performs structural validation:
    - NONE -> linked_entity_id must be None
    - TASK -> linked_entity_id required
    - COMMITMENT -> linked_entity_id required
    - calendar_slot_start and calendar_slot_end both present or both absent
    - timezone-aware when present
    - start < end

    Database lookup, entity existence, and ownership checks belong STRICTLY
    to Tier-1 Execution Preflight in the service layer.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_title: str = Field(min_length=1, max_length=500)
    exact_scheduled_at: datetime  # Bound UTC instant
    linked_entity_type: ReminderLinkType = Field(default=ReminderLinkType.NONE)
    linked_entity_id: Optional[str] = None
    calendar_slot_start: Optional[datetime] = None  # Informational interval metadata
    calendar_slot_end: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_structural_invariants(self) -> "ReminderCreateExecutionPrecondition":
        # 0. Timezone awareness check
        if self.exact_scheduled_at.tzinfo is None:
            raise ValueError("exact_scheduled_at must be timezone-aware.")

        # 1. Structural Phase 6F/6G ReminderLinkType validation
        if self.linked_entity_type == ReminderLinkType.NONE:
            if self.linked_entity_id is not None:
                raise ValueError("linked_entity_id must be None when linked_entity_type is NONE.")
        elif self.linked_entity_type in (ReminderLinkType.TASK, ReminderLinkType.COMMITMENT):
            if not self.linked_entity_id or not self.linked_entity_id.strip():
                raise ValueError(
                    f"linked_entity_id is required when linked_entity_type is {self.linked_entity_type.value}."
                )

        # 2. Structural calendar slot interval metadata validation
        has_start = self.calendar_slot_start is not None
        has_end = self.calendar_slot_end is not None
        if has_start != has_end:
            raise ValueError("calendar_slot_start and calendar_slot_end must both be present or both be absent.")

        if has_start and has_end:
            if self.calendar_slot_start.tzinfo is None or self.calendar_slot_end.tzinfo is None:
                raise ValueError("calendar_slot_start and calendar_slot_end must be timezone-aware.")
            if self.calendar_slot_start >= self.calendar_slot_end:
                raise ValueError("calendar_slot_start must be strictly earlier than calendar_slot_end.")

        return self


class MemoryCreateExecutionPrecondition(BaseModel):
    """
    Immutable execution precondition for creating an executive reflection memory.
    Content is server-held immutable. Provenance, type, and confidence are server-controlled.
    Extra fields are strictly forbidden.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    exact_content: str = Field(min_length=1, max_length=5000)
    context_tags: List[str] = Field(default_factory=list)
    source_horizon_type: str = Field(default="evening_review")


class HorizonOperationPrecondition(BaseModel):
    """
    Strictly discriminated execution precondition enforcing mutual exclusivity.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_type: Optional[HorizonOperationType] = None
    task_rollover: Optional[TaskRolloverPrecondition] = None
    reminder_create: Optional[ReminderCreateExecutionPrecondition] = None
    memory_create: Optional[MemoryCreateExecutionPrecondition] = None

    @model_validator(mode="after")
    def validate_mutual_exclusivity(self) -> "HorizonOperationPrecondition":
        payloads = [
            (HorizonOperationType.ROLLOVER_TASK, self.task_rollover),
            (HorizonOperationType.CREATE_REMINDER, self.reminder_create),
            (HorizonOperationType.CREATE_MEMORY, self.memory_create),
        ]
        populated = [(op_t, p) for op_t, p in payloads if p is not None]
        if len(populated) != 1:
            raise ValueError(f"Exactly one precondition payload must be populated, found {len(populated)}.")
        inferred_type = populated[0][0]
        if self.operation_type is not None and self.operation_type != inferred_type:
            raise ValueError(
                f"operation_type '{self.operation_type.value}' does not match populated payload '{inferred_type.value}'."
            )
        return self


class HorizonOperation(BaseModel):
    """
    Single discrete operation within a horizon proposal.
    `proposed_payload` is presentation-only; execution derives strictly from `precondition`.
    """
    index: int = 0
    operation_id: str = Field(default_factory=lambda: f"hop_{uuid.uuid4().hex[:8]}")
    operation_type: HorizonOperationType
    precondition: HorizonOperationPrecondition
    proposed_payload: Dict[str, Any] = Field(default_factory=dict)
    status: HorizonOperationStatus = Field(default=HorizonOperationStatus.PENDING)
    failure_error: Optional[str] = None
    created_entity_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_operation_consistency(self) -> "HorizonOperation":
        payload_matches = {
            HorizonOperationType.ROLLOVER_TASK: self.precondition.task_rollover,
            HorizonOperationType.CREATE_REMINDER: self.precondition.reminder_create,
            HorizonOperationType.CREATE_MEMORY: self.precondition.memory_create,
        }
        if payload_matches.get(self.operation_type) is None:
            raise ValueError(f"Precondition payload does not match operation_type '{self.operation_type.value}'.")
        return self


# ── Presentation Models ───────────────────────────────────────────────────────

class HorizonOperationPreview(BaseModel):
    operation_index: int
    operation_type: HorizonOperationType
    title: str
    details: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class HorizonProposalPresentation(BaseModel):
    proposal_id: str
    proposal_type: str
    expires_at: datetime
    summary: str
    focus_recommendations: List[DayPlanFocusRecommendation] = Field(default_factory=list)
    operations: List[HorizonOperationPreview] = Field(default_factory=list)
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)

    @property
    def recommendations(self) -> List[DayPlanFocusRecommendation]:
        return self.focus_recommendations

    @property
    def commitment_warnings(self) -> List[Dict[str, Any]]:
        warnings = []
        for op in self.operations:
            for w in op.warnings:
                warnings.append({
                    "commitment_id": op.details.get("source_commitment_id") or op.details.get("commitment_id"),
                    "warning": w,
                })
        return warnings


class DayPlanProposal(BaseModel):
    """
    Server-held, governed proposal for daily executive planning operations.
    """
    proposal_id: str = Field(default_factory=lambda: f"hpp_day_{uuid.uuid4().hex[:10]}")
    proposal_type: HorizonProposalType = Field(default=HorizonProposalType.DAY_PLAN)
    user_id: str
    target_date: Union[date, str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    status: HorizonProposalStatus = Field(default=HorizonProposalStatus.DRAFT)
    state_hash: str
    focus_recommendations: List[DayPlanFocusRecommendation] = Field(default_factory=list)
    ordered_operations: List[HorizonOperation] = Field(default_factory=list)
    failure_reason: Optional[HorizonFailureReason] = None
    applied_at: Optional[datetime] = None
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)

    @property
    def presentation(self) -> HorizonProposalPresentation:
        ops_preview = []
        for op in self.ordered_operations:
            w_list = op.proposed_payload.get("warnings", [])
            if not w_list and "warning" in op.proposed_payload:
                w_list = [op.proposed_payload["warning"]]
            ops_preview.append(
                HorizonOperationPreview(
                    operation_index=op.index,
                    operation_type=op.operation_type,
                    title=op.proposed_payload.get("title", op.operation_type.value),
                    details=op.proposed_payload,
                    warnings=w_list,
                )
            )
        return HorizonProposalPresentation(
            proposal_id=self.proposal_id,
            proposal_type=self.proposal_type.value,
            expires_at=self.expires_at,
            summary=f"Daily executive plan proposal with {len(self.ordered_operations)} operations.",
            focus_recommendations=self.focus_recommendations,
            operations=ops_preview,
            omitted_domains=self.omitted_domains,
        )


class EveningReviewProposal(BaseModel):
    """
    Server-held, governed proposal for evening executive horizon review operations.
    """
    proposal_id: str = Field(default_factory=lambda: f"hpp_eve_{uuid.uuid4().hex[:10]}")
    proposal_type: HorizonProposalType = Field(default=HorizonProposalType.EVENING_REVIEW)
    user_id: str
    target_date: Union[date, str]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    status: HorizonProposalStatus = Field(default=HorizonProposalStatus.DRAFT)
    state_hash: str
    ordered_operations: List[HorizonOperation] = Field(default_factory=list)
    failure_reason: Optional[HorizonFailureReason] = None
    applied_at: Optional[datetime] = None
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)

    @property
    def presentation(self) -> HorizonProposalPresentation:
        ops_preview = []
        for op in self.ordered_operations:
            w_list = op.proposed_payload.get("warnings", [])
            if not w_list and "warning" in op.proposed_payload:
                w_list = [op.proposed_payload["warning"]]
            ops_preview.append(
                HorizonOperationPreview(
                    operation_index=op.index,
                    operation_type=op.operation_type,
                    title=op.proposed_payload.get("title", op.operation_type.value),
                    details=op.proposed_payload,
                    warnings=w_list,
                )
            )
        return HorizonProposalPresentation(
            proposal_id=self.proposal_id,
            proposal_type=self.proposal_type.value,
            expires_at=self.expires_at,
            summary=f"Evening executive review proposal with {len(self.ordered_operations)} operations.",
            focus_recommendations=[],
            operations=ops_preview,
            omitted_domains=self.omitted_domains,
        )


# ── Read-Only Synthesis Models ────────────────────────────────────────────────

class DailyExecutivePlan(BaseModel):
    """
    Contextual diurnal executive synthesis. Read-only.
    """
    target_date: Union[date, str]
    user_id: str
    schedule_intervals: List[Dict[str, Any]] = Field(default_factory=list)
    open_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    active_commitments: List[Dict[str, Any]] = Field(default_factory=list)
    scheduled_reminders: List[Dict[str, Any]] = Field(default_factory=list)
    free_slots: List[Dict[str, Any]] = Field(default_factory=list)
    focus_recommendations: List[DayPlanFocusRecommendation] = Field(default_factory=list)
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)
    summary: str = ""

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self.schedule_intervals

    @property
    def tasks(self) -> List[Dict[str, Any]]:
        return self.open_tasks

    @property
    def commitments(self) -> List[Dict[str, Any]]:
        return self.active_commitments


class EveningHorizonReview(BaseModel):
    """
    Contextual evening review synthesis. Read-only.
    """
    target_date: Union[date, str]
    user_id: str
    completed_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    overdue_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    completed_commitments: List[Dict[str, Any]] = Field(default_factory=list)
    active_commitments: List[Dict[str, Any]] = Field(default_factory=list)
    scheduled_reminders: List[Dict[str, Any]] = Field(default_factory=list)
    completion_stats: Dict[str, Any] = Field(default_factory=dict)
    rollover_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    omitted_domains: List[Dict[str, str]] = Field(default_factory=list)
    summary: str = ""


# ── Canonical State Serialization & Hashing ───────────────────────────────────

def serialize_horizon_canonical_state(
    tasks: List[Task],
    commitments: List[Commitment],
) -> str:
    """
    Computes deterministic canonical JSON string representing referenced state.

    Temporal Rules:
    - Task.due_at (INSTANT): Normalized to UTC ISO-8601 instant string or null.
    - Commitment.target_date (DATE_TARGET): Extracted strictly as date-only 'YYYY-MM-DD'.
      NO timezone conversion, NO astimezone(), NO fabricated midnight instant.
    - None values remain null.
    """
    serialized_tasks = [
        {
            "id": t.id,
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "due_at": t.due_at.astimezone(timezone.utc).isoformat() if t.due_at else None,
            "title": t.title,
            "source_commitment_id": t.source_commitment_id,
        }
        for t in sorted(tasks, key=lambda x: x.id)
    ]

    serialized_commitments = [
        {
            "id": c.id,
            "status": c.status.value if hasattr(c.status, "value") else str(c.status),
            "title": c.title,
            "target_date": c.target_date.strftime("%Y-%m-%d") if c.target_date else None,
        }
        for c in sorted(commitments, key=lambda x: x.id)
    ]

    payload = {
        "tasks": serialized_tasks,
        "commitments": serialized_commitments,
    }

    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def compute_horizon_state_hash(
    tasks: List[Task],
    commitments: List[Commitment],
) -> str:
    """Computes SHA-256 hash of canonical state serialization."""
    canonical_json = serialize_horizon_canonical_state(tasks, commitments)
    return hashlib.sha256(canonical_json.encode('utf-8')).hexdigest()


def build_client_action(action: str, reminder: Reminder) -> Dict[str, Any]:
    """
    Constructs a deterministic, canonical client notification action (Phase 6B/6G).
    Action ID format: [action]:[reminder_id]:[canonical UTC updated_at with microsecond precision].
    """
    dt_updated = reminder.updated_at
    if dt_updated.tzinfo is None:
        dt_updated = dt_updated.replace(tzinfo=timezone.utc)
    else:
        dt_updated = dt_updated.astimezone(timezone.utc)
    canonical_updated_at = dt_updated.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    action_id = f"{action}:{reminder.id}:{canonical_updated_at}"

    sched_iso = reminder.scheduled_at.astimezone(timezone.utc).isoformat() if reminder.scheduled_at.tzinfo else reminder.scheduled_at.replace(tzinfo=timezone.utc).isoformat()

    return {
        "action": action,
        "action_id": action_id,
        "payload": {
            "reminder_id": reminder.id,
            "title": reminder.title,
            "scheduled_at": sched_iso,
            "task_id": reminder.task_id,
            "commitment_id": reminder.commitment_id,
            "notification_type": "reminder",
        }
    }
