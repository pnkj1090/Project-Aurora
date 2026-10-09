from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union
import zoneinfo
from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Strict Enums ─────────────────────────────────────────────────────────────

class RecoveryProposalStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    APPLIED = "APPLIED"
    ABORTED = "ABORTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class RecoveryExecutionStatus(str, Enum):
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class RecoveryOperationType(str, Enum):
    RESCHEDULE_TASK = "RESCHEDULE_TASK"
    RESCHEDULE_REMINDER = "RESCHEDULE_REMINDER"


# ── Strict UTC Datetime Validator ────────────────────────────────────────────

def validate_strict_utc(v: Any) -> datetime:
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if not isinstance(v, datetime):
        raise ValueError("Field must be a datetime instance or valid ISO8601 string")
    if v.tzinfo is None:
        raise ValueError("Datetime must be timezone-aware (UTC required)")
    if v.utcoffset() != timedelta(0):
        raise ValueError("Datetime offset must be strictly UTC (+00:00 or Z)")
    return v

StrictUTCDateTime = Annotated[datetime, Field(description="Strict UTC timezone-aware datetime")]


# ── Strict Typed Operation Payloads (Tasks and Reminders Only) ───────────────

class RescheduleTaskPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task_id: str = Field(..., min_length=1)
    new_due_at: StrictUTCDateTime
    expected_version: int = Field(..., ge=1)

    @field_validator("new_due_at", mode="before")
    @classmethod
    def _val_due(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


class RescheduleReminderPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reminder_id: str = Field(..., min_length=1)
    new_scheduled_at: StrictUTCDateTime

    @field_validator("new_scheduled_at", mode="before")
    @classmethod
    def _val_sched(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


RecoveryOperationPayload = Union[RescheduleTaskPayload, RescheduleReminderPayload]


# ── Precondition & Dependency Models ─────────────────────────────────────────

class RecoveryTaskPrecondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task_id: str = Field(..., min_length=1)
    expected_version: int = Field(..., ge=1)
    expected_status: str = Field(..., min_length=1)
    expected_due_at: Optional[StrictUTCDateTime] = None
    source_commitment_id: Optional[str] = None
    expected_state_hash: str = Field(..., min_length=64, max_length=64)

    @field_validator("expected_due_at", mode="before")
    @classmethod
    def _val_due(cls, v: Any) -> Optional[datetime]:
        return validate_strict_utc(v) if v is not None else None


class RecoveryReminderPrecondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reminder_id: str = Field(..., min_length=1)
    expected_scheduled_at: StrictUTCDateTime
    expected_status: str = Field(..., min_length=1)
    expected_state_hash: str = Field(..., min_length=64, max_length=64)

    @field_validator("expected_scheduled_at", mode="before")
    @classmethod
    def _val_sched(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


class CommitmentExternalDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    commitment_id: str = Field(..., min_length=1)
    type: Literal["DATE_TARGET"] = "DATE_TARGET"
    expected_target_date: StrictUTCDateTime
    linked_task_id: str = Field(..., min_length=1)

    @field_validator("expected_target_date", mode="before")
    @classmethod
    def _val_target(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


class WorkingHoursExternalDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    effective_timezone: str = Field(..., min_length=1)
    effective_dates: Tuple[str, ...] = Field(default_factory=tuple)
    working_hours_fingerprint: str = Field(..., min_length=64, max_length=64)


class CalendarExclusivityPrecondition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    window_start: StrictUTCDateTime
    window_end: StrictUTCDateTime
    calendar_event_fingerprint: str = Field(..., min_length=64, max_length=64)

    @field_validator("window_start", "window_end", mode="before")
    @classmethod
    def _val_window(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


# ── Authoritative Operation Model ─────────────────────────────────────────────

class ScheduleRecoveryOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    operation_id: str = Field(..., min_length=1)
    operation_type: RecoveryOperationType
    target_entity_id: str = Field(..., min_length=1)
    new_scheduled_time: StrictUTCDateTime
    payload: RecoveryOperationPayload
    task_precondition: Optional[RecoveryTaskPrecondition] = None
    reminder_precondition: Optional[RecoveryReminderPrecondition] = None

    @field_validator("new_scheduled_time", mode="before")
    @classmethod
    def _val_time(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


# ── Ephemeral Presentation Notes ──────────────────────────────────────────────

class EphemeralRecoveryNote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    note_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    details: str = Field(..., min_length=1)
    affected_entities: Tuple[str, ...] = Field(default_factory=tuple)
    generated_at: StrictUTCDateTime

    @field_validator("generated_at", mode="before")
    @classmethod
    def _val_gen(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


# ── Authoritative Proposal Model ─────────────────────────────────────────────

class ScheduleRecoveryProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    proposal_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    created_at: StrictUTCDateTime
    expires_at: StrictUTCDateTime
    status: RecoveryProposalStatus = RecoveryProposalStatus.DRAFT
    operations: Tuple[ScheduleRecoveryOperation, ...] = Field(default_factory=tuple)
    task_preconditions: Tuple[RecoveryTaskPrecondition, ...] = Field(default_factory=tuple)
    reminder_preconditions: Tuple[RecoveryReminderPrecondition, ...] = Field(default_factory=tuple)
    commitment_dependencies: Tuple[CommitmentExternalDependency, ...] = Field(default_factory=tuple)
    working_hours_dependency: WorkingHoursExternalDependency
    calendar_precondition: CalendarExclusivityPrecondition
    preconditions_hash: str = Field(..., min_length=64, max_length=64)
    ephemeral_notes: Tuple[EphemeralRecoveryNote, ...] = Field(default_factory=tuple)
    failure_reason: Optional[str] = None
    applied_at: Optional[StrictUTCDateTime] = None

    @field_validator("created_at", "expires_at", "applied_at", mode="before")
    @classmethod
    def _val_dates(cls, v: Any) -> Optional[datetime]:
        return validate_strict_utc(v) if v is not None else None


# ── Execution Diagnostics & Results ──────────────────────────────────────────

class RecoveryExecutionDiagnostics(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    preflight_checked_at: StrictUTCDateTime
    preflight_duration_ms: float = Field(..., ge=0.0)
    execution_duration_ms: float = Field(..., ge=0.0)
    total_duration_ms: float = Field(..., ge=0.0)
    presentation_notes: Tuple[EphemeralRecoveryNote, ...] = Field(default_factory=tuple)

    @field_validator("preflight_checked_at", mode="before")
    @classmethod
    def _val_chk(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


class RecoveryExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    execution_id: str = Field(..., min_length=1)
    proposal_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    status: RecoveryExecutionStatus
    started_at: StrictUTCDateTime
    completed_at: StrictUTCDateTime
    completed_operations: Tuple[str, ...] = Field(default_factory=tuple)
    failed_operation_id: Optional[str] = None
    failure_reason: Optional[str] = None
    execution_trace: Tuple[str, ...] = Field(default_factory=tuple)
    diagnostics: RecoveryExecutionDiagnostics

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def _val_times(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


# ── Recovery Evaluation Models ───────────────────────────────────────────────

class PreconditionMismatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    category: str = Field(..., min_length=1)
    entity_id: Optional[str] = None
    field: Optional[str] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    reason: str = Field(..., min_length=1)
    details: str = Field(..., min_length=1)


class RecoveryEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    valid: bool
    checked_at: StrictUTCDateTime
    mismatches: Tuple[PreconditionMismatch, ...] = Field(default_factory=tuple)

    @field_validator("checked_at", mode="before")
    @classmethod
    def _val_chk(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


# ── REST Request Models ──────────────────────────────────────────────────────

class ProposeScheduleRecoveryRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    task_ids: List[str] = Field(..., min_length=1, description="List of task IDs to recover")
    reminder_ids: Optional[List[str]] = Field(None, description="Optional reminder IDs to reschedule")
    target_window_start: StrictUTCDateTime = Field(..., description="Start of recovery window")
    target_window_end: StrictUTCDateTime = Field(..., description="End of recovery window")
    user_intent: Optional[str] = Field(None, description="Optional user intent description")
    client_preferred_timezone: Optional[str] = Field(None, description="Optional IANA timezone name")

    @field_validator("target_window_start", "target_window_end", mode="before")
    @classmethod
    def _val_window(cls, v: Any) -> datetime:
        return validate_strict_utc(v)


class CancelRecoveryProposalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    reason: Optional[str] = Field(None, description="Reason for cancellation")


class ApplyRecoveryProposalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    confirmed: bool = Field(..., description="Mandatory confirmation flag; must be true")
    client_context: Optional[Dict[str, Any]] = Field(None, description="Optional telemetry/client context")


class EvaluateRecoveryProposalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    proposal_id: str = Field(..., min_length=1, description="Proposal ID to dry-run evaluate")


# ── Complete Phase 6I Exception Taxonomy ─────────────────────────────────────

class Phase6IError(Exception):
    def __init__(self, message: str, error_code: str = "INTERNAL_ERROR"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class ProposalNotFoundError(Phase6IError):
    def __init__(self, proposal_id: str):
        super().__init__(f"Proposal {proposal_id} not found", "PROPOSAL_NOT_FOUND")


class ProposalExpiredError(Phase6IError):
    def __init__(self, proposal_id: str):
        super().__init__(f"Proposal {proposal_id} has expired", "PROPOSAL_EXPIRED")


class ProposalAlreadyTerminalError(Phase6IError):
    def __init__(self, proposal_id: str, status: str):
        super().__init__(f"Proposal {proposal_id} is already in terminal state {status}", "PROPOSAL_ALREADY_TERMINAL")


class ProposalStateConflictError(Phase6IError):
    def __init__(self, proposal_id: str, current_status: str, attempted_transition: str):
        super().__init__(f"Cannot transition proposal {proposal_id} from {current_status} to {attempted_transition}", "INVALID_STATE_TRANSITION")


class ActiveExecutionLockConflictError(Phase6IError):
    def __init__(self, user_id: str):
        super().__init__(f"Active execution currently in progress for user {user_id}", "ACTIVE_EXECUTION_LOCK_CONFLICT")


class ExecutionResultCapacityExceededError(Phase6IError):
    def __init__(self, capacity: int):
        super().__init__(f"Execution store capacity ({capacity}) exceeded and no terminal results eligible for eviction", "EXECUTION_RESULT_CAPACITY_EXCEEDED")


class ProposalCapacityExceededError(Phase6IError):
    def __init__(self, capacity: int):
        super().__init__(f"Proposal store capacity ({capacity}) reached", "PROPOSAL_CAPACITY_EXCEEDED")


class PreconditionFailedError(Phase6IError):
    def __init__(self, message: str, error_code: str = "PRECONDITION_FAILED"):
        super().__init__(message, error_code)


class PartialExecutionError(Phase6IError):
    def __init__(self, message: str, failed_operation_id: str, completed_operations: Tuple[str, ...]):
        super().__init__(message, "PARTIAL_EXECUTION_FAILURE")
        self.failed_operation_id = failed_operation_id
        self.completed_operations = completed_operations


class AuthorizationPrecedenceError(Phase6IError):
    def __init__(self, message: str):
        super().__init__(message, "FORBIDDEN")


class ResultNotFoundError(Phase6IError):
    def __init__(self, proposal_id: str):
        super().__init__(f"Execution result for proposal {proposal_id} not found", "RESULT_NOT_FOUND")
