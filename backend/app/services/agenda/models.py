from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class AgendaTemporalKind(str, Enum):
    """
    Phase 6D normalized temporal semantics.
    Strictly distinguishes time-occupying intervals, discrete point instants,
    and date-level target horizons.
    """
    INTERVAL = "interval"
    INSTANT = "instant"
    DATE_TARGET = "date_target"


class ConflictType(str, Enum):
    """
    Deterministic cross-domain temporal relationships and collisions.
    """
    EVENT_OVERLAP = "event_overlap"
    DEADLINE_DURING_EVENT = "deadline_during_event"
    REMINDER_DURING_EVENT = "reminder_during_event"
    TASK_DEADLINE_EXCEEDS_COMMITMENT_TARGET = "task_deadline_exceeds_commitment_target"
    TASK_OVERDUE = "task_overdue"
    COMMITMENT_OVERDUE = "commitment_overdue"


class ConflictSeverity(str, Enum):
    """
    Severity rating for detected temporal relationships.
    """
    INFO = "info"
    WARNING = "warning"


class AgendaConflict(BaseModel):
    """
    Normalized factual representation of a detected cross-domain conflict.
    """
    conflict_id: str = Field(..., description="Unique deterministic conflict identifier")
    conflict_type: ConflictType = Field(..., description="Classification of the conflict")
    severity: ConflictSeverity = Field(..., description="Severity level: INFO or WARNING")
    description: str = Field(..., description="Factual, data-minimized description of the collision")
    item_ids: List[str] = Field(default_factory=list, description="Public item_ids involved in the conflict")
    detected_at: datetime = Field(..., description="UTC timestamp of evaluation")
    suggested_action: Optional[str] = Field(default=None, description="Actionable advisory suggestion")


class AgendaItem(BaseModel):
    """
    Normalized chronological projection of a domain entity.
    Exposes only public-safe identifiers. Never leaks internal SQLite/DB UUIDs.
    """
    item_id: str = Field(..., description="Normalized public ID, format: {domain}:{public_entity_id}")
    source_domain: str = Field(..., description="'calendar' | 'tasks' | 'commitments' | 'reminders'")
    source_entity_id: str = Field(..., description="Public-safe underlying domain entity ID")
    display_title: str = Field(..., description="Sanitized entity title")
    temporal_kind: AgendaTemporalKind
    
    # Kind-specific temporal fields (mutually validated)
    start_at: Optional[datetime] = None       # Populated only for INTERVAL (UTC)
    end_at: Optional[datetime] = None         # Populated only for INTERVAL (UTC)
    instant_at: Optional[datetime] = None     # Populated only for INSTANT (UTC)
    target_date: Optional[date] = None        # Populated only for DATE_TARGET (date-only)
    
    is_all_day: bool = Field(default=False)
    status: str = Field(..., description="Domain status (e.g. pending, in_progress, scheduled)")
    priority: Optional[str] = None
    source_commitment_id: Optional[str] = None # Public commitment ID for linked tasks
    conflict_ids: List[str] = Field(default_factory=list)

    @field_validator("target_date", mode="before")
    @classmethod
    def reject_datetime_for_target_date(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            raise ValueError("target_date must be a date object, not a datetime.")
        return v

    @model_validator(mode="after")
    def validate_temporal_invariants(self) -> AgendaItem:
        if self.temporal_kind == AgendaTemporalKind.INTERVAL:
            if self.start_at is None or self.end_at is None:
                raise ValueError("INTERVAL items must have both start_at and end_at populated.")
            if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
                raise ValueError("start_at and end_at must be timezone-aware.")
            if self.end_at <= self.start_at:
                raise ValueError("end_at must be strictly after start_at for INTERVAL items.")
            if self.instant_at is not None:
                raise ValueError("instant_at must be None for INTERVAL items.")
            if self.target_date is not None:
                raise ValueError("target_date must be None for INTERVAL items.")

        elif self.temporal_kind == AgendaTemporalKind.INSTANT:
            if self.instant_at is None:
                raise ValueError("INSTANT items must have instant_at populated.")
            if self.instant_at.tzinfo is None:
                raise ValueError("instant_at must be timezone-aware.")
            if self.start_at is not None or self.end_at is not None:
                raise ValueError("start_at and end_at must be None for INSTANT items.")
            if self.target_date is not None:
                raise ValueError("target_date must be None for INSTANT items.")

        elif self.temporal_kind == AgendaTemporalKind.DATE_TARGET:
            if self.target_date is None:
                raise ValueError("DATE_TARGET items must have target_date populated.")
            # In Python, datetime is a subclass of date. Ensure it is strictly date, not datetime.
            if isinstance(self.target_date, datetime) or type(self.target_date) is not date:
                raise ValueError("target_date must be a date object, not a datetime.")
            if self.start_at is not None or self.end_at is not None:
                raise ValueError("start_at and end_at must be None for DATE_TARGET items.")
            if self.instant_at is not None:
                raise ValueError("instant_at must be None for DATE_TARGET items.")

        return self


class FreeSlot(BaseModel):
    """
    Ephemeral recommendation of an available work interval.
    Does NOT represent a persisted work block.
    """
    start_at: datetime = Field(..., description="UTC start datetime of the free gap")
    end_at: datetime = Field(..., description="UTC end datetime of the free gap")
    duration_minutes: int = Field(..., description="Total duration of the gap in minutes")


class DailyAgendaResponse(BaseModel):
    """
    Unified daily agenda response projection.
    """
    target_date: str = Field(..., description="Evaluation date string (YYYY-MM-DD)")
    timezone: str = Field(..., description="Effective Evaluation Timezone IANA identifier")
    items: List[AgendaItem] = Field(default_factory=list, description="Unified chronological agenda items")
    conflicts: List[AgendaConflict] = Field(default_factory=list, description="Detected temporal conflicts")
    free_slots: List[FreeSlot] = Field(default_factory=list, description="Computed feasible working gaps")
    omitted_sources: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Domains omitted with reasons (e.g. UNAUTHORIZED, NO_SNAPSHOT_REPORTED)"
    )


class ProposedSlotResponse(BaseModel):
    """
    Deterministic response for propose_schedule_slot.
    """
    feasible: bool = Field(..., description="Whether a feasible slot meeting constraints exists")
    task_id: str = Field(..., description="Public task ID")
    task_title: str = Field(..., description="Sanitized task title")
    deadline_utc: Optional[datetime] = Field(default=None, description="Task deadline in UTC, if set")
    proposed_slots: List[FreeSlot] = Field(default_factory=list, description="Ranked feasible slots")
    warnings: List[str] = Field(default_factory=list, description="Domain advisory warnings")
    max_available_duration_minutes: int = Field(default=0, description="Longest continuous free gap found")
    reason: Optional[str] = Field(default=None, description="Diagnostic refusal reason if infeasible")


class ConflictScanResponse(BaseModel):
    """
    Response model for detect_schedule_conflicts.
    """
    conflicts: List[AgendaConflict] = Field(default_factory=list, description="Detected conflicts")
    total_conflicts: int = Field(..., description="Total number of detected conflicts")
    evaluated_window_start: datetime = Field(..., description="UTC start of evaluated window")
    evaluated_window_end: datetime = Field(..., description="UTC end of evaluated window")
    omitted_domains: List[str] = Field(default_factory=list, description="Domains omitted from evaluation")
