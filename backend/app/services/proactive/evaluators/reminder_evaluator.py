"""
Phase 5I — ReminderProposalEvaluator.

Inspects scheduled reminders on-demand and deterministically generates OPEN_REMINDER proposals:
- Past/overdue scheduled reminder (scheduled_at < now_utc):
  OPEN_REMINDER, MEDIUM priority, TIME trigger.

Invariants:
- Strictly checks PermissionType.REMINDERS. If not authorized, returns [] immediately.
- Evaluates strictly on-demand; no background daemon, scheduler, or polling.
- Reads existing reminders only.
- Strictly NO reminder creation.
- Strictly NO notification scheduling (no UNUserNotificationCenter, no APNs).
- Evaluators MUST NOT generate state-changing actions (CANCEL_REMINDER, etc.).
- Reuses existing ReminderManager.
"""
from datetime import datetime, timezone
from typing import List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.reminders import ReminderManager, get_reminder_manager, ReminderStatus
from app.services.proactive.evaluators.base import BaseSubsystemEvaluator
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveAction,
    ProactivePriority,
    ProactiveTrigger,
    ProactiveStatus,
)


class ReminderProposalEvaluator(BaseSubsystemEvaluator):
    """
    On-demand evaluator for reminder-related proactive suggestions.
    """

    def __init__(
        self,
        reminder_manager: Optional[ReminderManager] = None,
        permission_manager: Optional[PermissionManager] = None,
    ):
        self._reminder_manager = reminder_manager
        self._permission_manager = permission_manager or default_permission_manager

    @property
    def reminder_manager(self) -> ReminderManager:
        return self._reminder_manager or get_reminder_manager()

    async def evaluate_proposals(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> List[ProactiveCandidate]:
        # 1. Strict permission check for REMINDERS
        is_auth = await self._permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)
        if not is_auth:
            return []

        now_utc = datetime.now(timezone.utc)

        # 2. Query scheduled reminders
        reminders = await self.reminder_manager.list_reminders(
            user_id=user_id,
            statuses=[ReminderStatus.SCHEDULED],
            limit=50,
        )

        candidates: List[ProactiveCandidate] = []

        for reminder in reminders:
            rem_utc = reminder.scheduled_at if reminder.scheduled_at.tzinfo else reminder.scheduled_at.replace(tzinfo=timezone.utc)

            # Past/overdue scheduled reminder
            if rem_utc < now_utc:
                candidate = ProactiveCandidate(
                    user_id=user_id,
                    source="reminders",
                    trigger=ProactiveTrigger.TIME,
                    action=ProactiveAction.OPEN_REMINDER,
                    priority=ProactivePriority.MEDIUM,
                    status=ProactiveStatus.PENDING,
                    title=f"Scheduled reminder: {reminder.title}",
                    message=f"Reminder '{reminder.title}' was scheduled for {rem_utc.strftime('%H:%M UTC')}.",
                    reference_id=reminder.id,
                    action_payload={"reminder_id": reminder.id},
                )
                candidates.append(candidate)

        return candidates
