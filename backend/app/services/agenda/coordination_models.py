from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.tools.base import ToolRiskLevel
from app.services.agenda.models import AgendaConflict


class StrategyCategory(str, Enum):
    """
    Categorization of conflict resolution and schedule coordination strategies.
    """
    RESOLUTION = "resolution"
    MITIGATION = "mitigation"
    INFORMATION = "information"


class ProposalStatus(str, Enum):
    """
    Lifecycle states of a SelectedCoordinationProposal.
    """
    PENDING = "pending"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    PARTIALLY_EXECUTED = "partially_executed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ProposalFailureReason(str, Enum):
    """
    Exhaustive failure taxonomy for Phase 6E execution guards.
    """
    PERMISSION_REVOKED = "PERMISSION_REVOKED"
    NO_CALENDAR_SNAPSHOT = "NO_CALENDAR_SNAPSHOT"
    STALE_CALENDAR_SNAPSHOT = "STALE_CALENDAR_SNAPSHOT"
    STALE_CALENDAR_STATE = "STALE_CALENDAR_STATE"
    STALE_STATE_DETECTED = "STALE_STATE_DETECTED"
    OPERATION_PRECONDITION_FAILED = "OPERATION_PRECONDITION_FAILED"
    OPERATION_FAILED = "OPERATION_FAILED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"


class CoordinationOperationType(str, Enum):
    """
    Governed operations permitted within a coordination proposal.
    """
    UPDATE_TASK_DUE_DATE = "update_task_due_date"
    CREATE_REMINDER = "create_reminder"


class OperationExecutionStatus(str, Enum):
    """
    Individual operation execution status.
    """
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class CalendarEventExecutionPrecondition(BaseModel):
    """
    Server-held expected facts for referenced calendar events.
    Strictly forbids unexpected fields.
    """
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., description="Referenced calendar event ID")
    expected_start_at: datetime = Field(..., description="Expected timezone-aware start datetime (UTC)")
    expected_end_at: datetime = Field(..., description="Expected timezone-aware end datetime (UTC)")
    expected_all_day: bool = Field(..., description="Expected all-day status")
    expected_fingerprint: str = Field(..., description="SHA-256 canonical hash of event facts")

    @field_validator("expected_start_at", "expected_end_at", mode="after")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Precondition datetimes must be timezone-aware")
        return v


class TaskUpdateExecutionPrecondition(BaseModel):
    """
    Authoritative state precondition for updating a task's due date.
    Strictly forbids unexpected fields.
    """
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description="Target public task ID")
    expected_version: str = Field(..., description="Expected task version/updated_at token")
    expected_status: str = Field(..., description="Expected task status (e.g. pending)")
    expected_due_at: Optional[datetime] = Field(default=None, description="Expected existing due_at")
    effective_duration_minutes: int = Field(..., ge=15, description="Bound resolved duration in minutes (>= 15)")
    linked_commitment_id: Optional[str] = Field(default=None, description="Linked commitment ID if any")
    expected_commitment_target_date: Optional[date] = Field(default=None, description="Expected commitment target date")
    expected_commitment_version: Optional[str] = Field(default=None, description="Expected commitment version/updated_at")
    calendar_preconditions: List[CalendarEventExecutionPrecondition] = Field(
        default_factory=list,
        description="Calendar event facts that must hold true at dispatch"
    )

    @field_validator("expected_commitment_target_date", mode="before")
    @classmethod
    def reject_datetime_for_target_date(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            raise ValueError("expected_commitment_target_date must be a date, not a datetime")
        return v


class ReminderCreateExecutionPrecondition(BaseModel):
    """
    Authoritative state precondition for creating a linked reminder.
    Strictly forbids unexpected fields.
    """
    model_config = ConfigDict(extra="forbid")

    linked_entity_type: Optional[str] = Field(default=None, description="Target entity type, e.g. 'task'")
    linked_entity_id: Optional[str] = Field(default=None, description="Target entity ID")
    expected_linked_version: Optional[str] = Field(default=None, description="Expected linked entity version")
    expected_linked_status: Optional[str] = Field(default=None, description="Expected linked entity status")
    proposed_scheduled_at: datetime = Field(..., description="Proposed reminder fire time in UTC")
    calendar_preconditions: List[CalendarEventExecutionPrecondition] = Field(
        default_factory=list,
        description="Calendar event facts that must hold true at dispatch"
    )

    @field_validator("proposed_scheduled_at", mode="after")
    @classmethod
    def validate_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Precondition datetimes must be timezone-aware")
        return v


class OperationExecutionPrecondition(BaseModel):
    """
    Polymorphic typed container for operation execution preconditions.
    Strictly forbids unexpected fields.
    """
    model_config = ConfigDict(extra="forbid")

    operation_type: CoordinationOperationType
    task_update: Optional[TaskUpdateExecutionPrecondition] = None
    reminder_create: Optional[ReminderCreateExecutionPrecondition] = None

    @model_validator(mode="after")
    def validate_precondition_shape(self) -> OperationExecutionPrecondition:
        if self.operation_type == CoordinationOperationType.UPDATE_TASK_DUE_DATE:
            if self.task_update is None:
                raise ValueError("task_update precondition must be provided for UPDATE_TASK_DUE_DATE")
            if self.reminder_create is not None:
                raise ValueError("reminder_create must be None for UPDATE_TASK_DUE_DATE")
        elif self.operation_type == CoordinationOperationType.CREATE_REMINDER:
            if self.reminder_create is None:
                raise ValueError("reminder_create precondition must be provided for CREATE_REMINDER")
            if self.task_update is not None:
                raise ValueError("task_update must be None for CREATE_REMINDER")
        return self


class DisplayOperationPreview(BaseModel):
    """
    Read-only display representation of an operation inside a StrategyOption.
    Non-executable.
    """
    operation_type: CoordinationOperationType
    target_entity_id: str
    display_summary: str
    field_changes: Dict[str, Any] = Field(default_factory=dict)


class StrategyOption(BaseModel):
    """
    Non-executable strategy alternative presented to the user.
    """
    strategy_id: str
    analysis_id: str
    conflict_id: str
    category: StrategyCategory
    name: str
    description: str
    effective_duration_minutes: int = Field(..., ge=15)
    is_available: bool = True
    missing_permissions: List[str] = Field(default_factory=list)
    feasibility_proof: Optional[str] = None
    preview_operations: List[DisplayOperationPreview] = Field(default_factory=list)
    tradeoffs: Optional[str] = None


class AnalysisResult(BaseModel):
    """
    Read-only output of conflict and coordination analysis.
    Strictly non-executable.
    """
    analysis_id: str
    conversation_id: Optional[str] = None
    execution_id: Optional[str] = None
    user_id: str
    target_date: date
    timezone: str
    conflicts: List[AgendaConflict] = Field(default_factory=list)
    strategy_options: List[StrategyOption] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime
    is_superseded: bool = False


class CalendarSnapshotProvenance(BaseModel):
    """
    Provenance tracking for calendar events evaluated during planning.
    """
    snapshot_timestamp: datetime
    relevant_event_ids: List[str] = Field(default_factory=list)
    snapshot_version: Optional[str] = None


class ProposedOperation(BaseModel):
    """
    Server-held, validated operation within an executable proposal.
    Strictly forbids unexpected fields.
    """
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    operation_type: CoordinationOperationType
    target_entity_id: str
    before_state: Dict[str, Any]
    proposed_state: Dict[str, Any]
    required_permission: PermissionType
    is_calendar_dependent: bool
    precondition: OperationExecutionPrecondition
    risk_level: ToolRiskLevel
    status: OperationExecutionStatus = OperationExecutionStatus.PENDING
    failure_error: Optional[str] = None
    reason: str


class SelectedCoordinationProposal(BaseModel):
    """
    Authoritative server-held proposal constructed ONLY when a user selects a strategy.
    Requires exactly one composite confirmation.
    """
    proposal_id: str
    analysis_id: str
    execution_id: str
    selected_strategy_id: str
    ordered_operations: List[ProposedOperation] = Field(default_factory=list)
    state_hash: str
    calendar_provenance: CalendarSnapshotProvenance
    required_permissions: List[PermissionType] = Field(default_factory=list)
    risk_level: ToolRiskLevel
    status: ProposalStatus = ProposalStatus.PENDING
    failure_reason: Optional[ProposalFailureReason] = None
    expires_at: datetime
    user_id: str = DEFAULT_LOCAL_USER_ID
    conversation_id: Optional[str] = None
    created_at: Optional[datetime] = None


# --- REST Request & Response Models ---

class CoordinateScheduleRequest(BaseModel):
    """
    REST request to analyze and allocate tasks into available schedule slots.
    """
    target_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Local date YYYY-MM-DD")
    timezone: Optional[str] = Field(None, min_length=2, max_length=50, description="IANA timezone")
    work_start_hour: int = Field(9, ge=0, le=23, description="Start of working hours (0-23)")
    work_end_hour: int = Field(18, ge=0, le=23, description="End of working hours (0-23)")
    default_duration_minutes: int = Field(30, ge=15, description="Default duration in minutes (>= 15)")
    task_ids: Optional[List[str]] = Field(None, description="Specific task IDs to coordinate; all if omitted")


class AllocatedTaskSlot(BaseModel):
    """
    Ephemeral task allocation into a free working gap.
    Does NOT imply task deadline modification.
    """
    task_id: str
    task_title: str
    allocated_start_at: datetime
    allocated_end_at: datetime
    duration_minutes: int
    due_at: Optional[datetime] = None
    status: str


class CoordinateScheduleResponse(BaseModel):
    """
    Read-only output of schedule coordination analysis.
    Produces zero database mutations and zero client actions.
    """
    target_date: str
    timezone: str
    allocations: List[AllocatedTaskSlot] = Field(default_factory=list)
    unallocated_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    conflicts: List[AgendaConflict] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ResolveConflictsRequest(BaseModel):
    """
    REST request to scan for conflicts and generate read-only strategy options.
    """
    target_date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Local date YYYY-MM-DD")
    timezone: Optional[str] = Field(None, min_length=2, max_length=50, description="IANA timezone")
    window_hours: int = Field(24, ge=1, le=168, description="Lookahead window in hours (1-168)")
    default_duration_minutes: int = Field(30, ge=15, description="Default duration in minutes (>= 15)")
    work_start_hour: int = Field(9, ge=0, le=23, description="Start of working hours (0-23)")
    work_end_hour: int = Field(18, ge=0, le=23, description="End of working hours (0-23)")


class ResolveConflictsResponse(BaseModel):
    """
    Read-only output of conflict resolution analysis.
    Contains non-executable strategy alternatives.
    """
    analysis_id: str
    target_date: str
    timezone: str
    conflicts_count: int
    strategy_options: List[StrategyOption] = Field(default_factory=list)
