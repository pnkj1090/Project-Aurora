"""
Phase 5I — TaskProposalEvaluator.

Inspects active tasks on-demand and deterministically generates OPEN_TASK proposals:
- Overdue tasks (due_at < now_utc): OPEN_TASK, HIGH priority, TASK_DUE trigger.
- Approaching tasks (now_utc <= due_at <= now_utc + 2h): OPEN_TASK, MEDIUM priority, TASK_DUE trigger.

Invariants:
- Evaluates strictly on-demand; no background daemon or polling.
- Does NOT generate state-changing actions (COMPLETE_TASK, etc.).
- Reuses existing TaskManager.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.tasks import TaskManager, get_task_manager, TaskStatus
from app.services.proactive.evaluators.base import BaseSubsystemEvaluator
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveAction,
    ProactivePriority,
    ProactiveTrigger,
    ProactiveStatus,
)


class TaskProposalEvaluator(BaseSubsystemEvaluator):
    """
    On-demand evaluator for task-related proactive suggestions.
    """

    def __init__(self, task_manager: Optional[TaskManager] = None):
        self._task_manager = task_manager

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager or get_task_manager()

    async def evaluate_proposals(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> List[ProactiveCandidate]:
        now_utc = datetime.now(timezone.utc)
        approaching_threshold = now_utc + timedelta(hours=2)

        # Retrieve active tasks
        tasks = await self.task_manager.list_tasks(
            user_id=user_id,
            statuses=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS],
            limit=50,
        )

        candidates: List[ProactiveCandidate] = []

        for task in tasks:
            if not task.due_at:
                continue

            task_due_utc = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=timezone.utc)

            # 1. Overdue task
            if task_due_utc < now_utc:
                candidate = ProactiveCandidate(
                    user_id=user_id,
                    source="tasks",
                    trigger=ProactiveTrigger.TASK_DUE,
                    action=ProactiveAction.OPEN_TASK,
                    priority=ProactivePriority.HIGH,
                    status=ProactiveStatus.PENDING,
                    title=f"Task overdue: {task.title}",
                    message=f"'{task.title}' was due on {task_due_utc.strftime('%Y-%m-%d %H:%M UTC')}.",
                    reference_id=task.id,
                    action_payload={"task_id": task.id},
                )
                candidates.append(candidate)

            # 2. Approaching task (due within next 2 hours)
            elif now_utc <= task_due_utc <= approaching_threshold:
                candidate = ProactiveCandidate(
                    user_id=user_id,
                    source="tasks",
                    trigger=ProactiveTrigger.TASK_DUE,
                    action=ProactiveAction.OPEN_TASK,
                    priority=ProactivePriority.MEDIUM,
                    status=ProactiveStatus.PENDING,
                    title=f"Task due soon: {task.title}",
                    message=f"'{task.title}' is due in less than 2 hours ({task_due_utc.strftime('%H:%M UTC')}).",
                    reference_id=task.id,
                    action_payload={"task_id": task.id},
                )
                candidates.append(candidate)

        return candidates
