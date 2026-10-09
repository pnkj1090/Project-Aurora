from __future__ import annotations

import logging
from typing import List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.proactive.evaluators.base import BaseSubsystemEvaluator
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveAction,
    ProactivePriority,
    ProactiveTrigger,
    ProactiveStatus,
)
from app.services.agenda import (
    AgendaService,
    get_agenda_service,
    ConflictType,
)

logger = logging.getLogger("aurora.proactive.conflict_evaluator")

# Explicit candidate allowlist for Phase 6D:
# EVENT_OVERLAP and REMINDER_DURING_EVENT are strictly excluded by default to avoid alert fatigue.
ALLOWLISTED_CONFLICT_TYPES = {
    ConflictType.DEADLINE_DURING_EVENT,
    ConflictType.TASK_DEADLINE_EXCEEDS_COMMITMENT_TARGET,
    ConflictType.TASK_OVERDUE,
}


class ScheduleConflictEvaluator(BaseSubsystemEvaluator):
    """
    On-demand evaluator for cross-domain schedule collisions.
    Reuses AgendaService's single conflict detection engine directly.
    Filters detected conflicts through an explicit candidate allowlist gate.
    Produces zero autonomous mutations.
    """

    def __init__(self, agenda_service: Optional[AgendaService] = None):
        self._agenda_service = agenda_service

    @property
    def service(self) -> AgendaService:
        return self._agenda_service or get_agenda_service()

    async def evaluate_proposals(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> List[ProactiveCandidate]:
        try:
            scan = await self.service.detect_conflicts(user_id=user_id, window_hours=24)
        except Exception:
            logger.debug("ScheduleConflictEvaluator skipped: AgendaService query failed or unauthorized", exc_info=True)
            return []

        candidates: List[ProactiveCandidate] = []

        for conflict in scan.conflicts:
            # Enforce allowlist gate
            if conflict.conflict_type not in ALLOWLISTED_CONFLICT_TYPES:
                continue

            # Extract task ID if present in item_ids
            task_id = None
            for it in conflict.item_ids:
                if it.startswith("tasks:"):
                    task_id = it.split(":", 1)[1]
                    break

            if conflict.conflict_type == ConflictType.DEADLINE_DURING_EVENT:
                candidates.append(ProactiveCandidate(
                    user_id=user_id,
                    source="calendar",
                    trigger=ProactiveTrigger.CALENDAR_EVENT,
                    action=ProactiveAction.OPEN_TASK if task_id else ProactiveAction.OPEN_CALENDAR_EVENT,
                    priority=ProactivePriority.HIGH,
                    status=ProactiveStatus.PENDING,
                    title="Schedule Conflict: Task Deadline During Meeting",
                    message=conflict.description,
                    reference_id=conflict.conflict_id,
                    action_payload={"task_id": task_id} if task_id else None,
                    metadata={"conflict_id": conflict.conflict_id, "conflict_type": conflict.conflict_type.value},
                ))

            elif conflict.conflict_type == ConflictType.TASK_DEADLINE_EXCEEDS_COMMITMENT_TARGET:
                candidates.append(ProactiveCandidate(
                    user_id=user_id,
                    source="tasks",
                    trigger=ProactiveTrigger.TASK_DUE,
                    action=ProactiveAction.OPEN_TASK,
                    priority=ProactivePriority.MEDIUM,
                    status=ProactiveStatus.PENDING,
                    title="Schedule Inconsistency: Deadline Exceeds Target",
                    message=conflict.description,
                    reference_id=conflict.conflict_id,
                    action_payload={"task_id": task_id} if task_id else None,
                    metadata={"conflict_id": conflict.conflict_id, "conflict_type": conflict.conflict_type.value},
                ))

            elif conflict.conflict_type == ConflictType.TASK_OVERDUE:
                candidates.append(ProactiveCandidate(
                    user_id=user_id,
                    source="tasks",
                    trigger=ProactiveTrigger.TASK_DUE,
                    action=ProactiveAction.OPEN_TASK,
                    priority=ProactivePriority.HIGH,
                    status=ProactiveStatus.PENDING,
                    title="Task Overdue",
                    message=conflict.description,
                    reference_id=conflict.conflict_id,
                    action_payload={"task_id": task_id} if task_id else None,
                    metadata={"conflict_id": conflict.conflict_id, "conflict_type": conflict.conflict_type.value},
                ))

        return candidates
