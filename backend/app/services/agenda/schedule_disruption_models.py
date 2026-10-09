from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field


class Phase6JError(Exception):
    """Base error for Phase 6J disruption diagnostic errors."""

    def __init__(self, message: str, error_code: str = "SCHEDULE_DISRUPTION_ERROR"):
        super().__init__(message)
        self.error_code = error_code


class TaskDomainUnavailableError(Phase6JError):
    """Raised when PermissionType.TASKS is not AUTHORIZED."""

    def __init__(
        self,
        message: str = "Permission 'tasks' is not authorized. Schedule disruption diagnosis cannot be evaluated without authoritative access to the primary task workload.",
        error_code: str = "TASK_DOMAIN_UNAVAILABLE",
    ):
        super().__init__(message, error_code=error_code)


class WorkingHoursUnresolvableError(Phase6JError):
    """Raised when working hours validation or resolution fails."""

    def __init__(
        self,
        message: str = "Working hours could not be resolved or validated.",
        error_code: str = "WORKING_HOURS_UNRESOLVABLE",
    ):
        super().__init__(message, error_code=error_code)


class CalendarStateError(Phase6JError):
    """Raised when calendar state is missing, stale, saturated, or retrieval fails."""

    def __init__(
        self,
        message: str,
        error_code: str,
    ):
        super().__init__(message, error_code=error_code)


class DiscoveryUniverseSaturatedError(Phase6JError):
    """Raised when active entity count exceeds the safety ceiling of 500 items."""

    def __init__(
        self,
        message: str,
        error_code: str,
    ):
        super().__init__(message, error_code=error_code)


class InvalidWindowError(Phase6JError):
    """Raised when evaluation window is invalid or exceeds maximum span."""

    def __init__(
        self,
        message: str,
        error_code: str = "INVALID_WINDOW",
    ):
        super().__init__(message, error_code=error_code)


from app.services.agenda.service import InvalidTimezoneError as AgendaInvalidTimezoneError


class InvalidTimezoneError(Phase6JError, AgendaInvalidTimezoneError):
    """Raised when client provides an invalid or non-IANA timezone identifier."""

    def __init__(
        self,
        message: str = "Invalid IANA timezone identifier provided.",
        error_code: str = "INVALID_TIMEZONE",
    ):
        super().__init__(message, error_code=error_code)


class TaskJeopardyReason(str, Enum):
    OVERDUE = "overdue"
    DEADLINE_DURING_EVENT = "deadline_during_event"
    COMMITMENT_OVERRUN_RISK = "commitment_overrun_risk"
    WORKING_HOURS_OVERFLOW = "working_hours_overflow"
    UNSCHEDULED_WITH_TARGET = "unscheduled_with_target"


class TaskJeopardyItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(..., description="Unique task identifier")
    title: str = Field(..., description="Task title")
    current_due_at: Optional[datetime] = Field(None, description="Current UTC due datetime")
    linked_commitment_id: Optional[str] = Field(None, description="Associated commitment ID if linked")
    commitment_target_date: Optional[date] = Field(None, description="Target date of linked commitment")
    jeopardy_reasons: Tuple[TaskJeopardyReason, ...] = Field(..., description="Deterministic jeopardy causes")
    estimated_duration_minutes: int = Field(..., description="Duration resolved via ScheduleCoordinationService")


class ScheduleDisruptionSeverity(str, Enum):
    NONE = "none"
    MINOR = "minor"
    MODERATE = "moderate"
    CRITICAL = "critical"


class ScheduleDisruptionDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnostic_id: str = Field(..., description="Unique server-generated diagnostic identifier: diag_<16-hex>")
    computed_at: datetime = Field(..., description="UTC timestamp of computation")
    evaluation_window_start: datetime = Field(..., description="Window start UTC")
    evaluation_window_end: datetime = Field(..., description="Window end UTC")
    timezone: str = Field(..., description="Effective evaluation timezone")
    severity: ScheduleDisruptionSeverity = Field(..., description="Deterministic disruption severity")
    total_working_minutes: int = Field(..., description="Total nominal working minutes in window")
    available_working_minutes: int = Field(..., description="Working minutes minus union calendar busy time")
    required_task_minutes: int = Field(
        ...,
        description="Sum of resolved durations of qualifying tasks, comprising both historical overdue workload debt (due_at < now_utc) and workload due within the evaluation window."
    )
    focus_capacity_deficit_minutes: int = Field(..., description="max(0, required - available)")
    overdue_task_count: int = Field(..., description="Count of overdue qualifying tasks where due_at < now_utc")
    jeopardy_tasks: Tuple[TaskJeopardyItem, ...] = Field(..., description="Ranked tasks in jeopardy")
    recommended_recovery_task_ids: Tuple[str, ...] = Field(..., description="Server-derived candidate task IDs")
    recommended_recovery_reminder_ids: Tuple[str, ...] = Field(..., description="Server-derived candidate reminder IDs")
    omitted_domains: Tuple[str, ...] = Field(..., description="Domains omitted due to permissions or unverified state")


class DiagnoseScheduleDisruptionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target_window_start: Optional[datetime] = Field(None, description="Start of evaluation window (default: now UTC)")
    target_window_end: Optional[datetime] = Field(None, description="End of evaluation window (default: now + 48h)")
    client_preferred_timezone: Optional[str] = Field(None, description="Optional IANA timezone identifier")
    include_reminders: bool = Field(False, description="Whether to include reminders in candidate discovery")
