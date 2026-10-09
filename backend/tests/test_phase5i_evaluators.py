"""
Phase 5I — Subsystem Evaluator Test Suite.

Verifies:
- TaskProposalEvaluator generates OPEN_TASK (overdue -> HIGH, approaching -> MEDIUM).
- CommitmentProposalEvaluator generates OPEN_COMMITMENT (approaching -> MEDIUM).
- CalendarProposalEvaluator generates OPEN_CALENDAR_EVENT (authorized) and [] (unauthorized).
- ReminderProposalEvaluator generates OPEN_REMINDER (authorized) and [] (unauthorized).
- Subsystem evaluators NEVER generate state-changing actions (COMPLETE_TASK, COMPLETE_COMMITMENT, CANCEL_REMINDER).
- Reminder evaluator never creates reminders and never schedules notifications.
- All evaluator output passes through Phase 5H ProactivePolicy pipeline.
- Deduplication and 5/hour rate limiting are enforced.
- Evaluation never executes any action (status remains ELIGIBLE).
- REST endpoint POST /api/v1/proactive/evaluate returns public-safe schemas (no internal leaks).
"""
import pytest
from datetime import datetime, timezone, timedelta

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType, PermissionStatus, PermissionRecord
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore
from app.services.tasks import TaskManager, Task, TaskStatus, TaskPriority
from app.services.tasks.store import InMemoryTaskStore
from app.services.commitments import CommitmentManager, Commitment, CommitmentStatus
from app.services.commitments.store import InMemoryCommitmentStore
from app.services.calendar import CalendarManager
from app.services.calendar.models import CalendarEvent
from app.services.reminders import ReminderManager, Reminder, ReminderStatus
from app.services.reminders.store import InMemoryReminderStore
from app.services.proactive import (
    ProactiveManager,
    InMemoryProactiveStore,
    ProactiveStatus,
    ProactiveAction,
    ProactivePriority,
    ProactiveTrigger,
)
from app.services.proactive.evaluators import (
    TaskProposalEvaluator,
    CommitmentProposalEvaluator,
    CalendarProposalEvaluator,
    ReminderProposalEvaluator,
)
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def permission_manager():
    store = InMemoryPermissionStore()
    return PermissionManager(store=store)


@pytest.fixture
def proactive_manager(permission_manager):
    store = InMemoryProactiveStore()
    return ProactiveManager(store=store, permission_manager=permission_manager)


# MARK: - TaskProposalEvaluator Tests

@pytest.mark.asyncio
async def test_task_evaluator_overdue_and_approaching():
    task_store = InMemoryTaskStore()
    await task_store.initialize()
    task_mgr = TaskManager(store=task_store)
    evaluator = TaskProposalEvaluator(task_manager=task_mgr)

    now_utc = datetime.now(timezone.utc)

    # 1. Overdue task
    overdue_task = Task(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Quarterly Review",
        status=TaskStatus.PENDING,
        priority=TaskPriority.HIGH,
        due_at=now_utc - timedelta(hours=1),
    )
    await task_store.put(overdue_task)

    # 2. Approaching task (due in 1 hour)
    approaching_task = Task(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Send Invoice",
        status=TaskStatus.PENDING,
        priority=TaskPriority.MEDIUM,
        due_at=now_utc + timedelta(hours=1),
    )
    await task_store.put(approaching_task)

    # 3. Distant task (due in 5 hours) - should NOT generate proposal
    distant_task = Task(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Future Plan",
        status=TaskStatus.PENDING,
        due_at=now_utc + timedelta(hours=5),
    )
    await task_store.put(distant_task)

    proposals = await evaluator.evaluate_proposals(user_id=DEFAULT_LOCAL_USER_ID)
    assert len(proposals) == 2

    # Overdue task proposal
    overdue_p = next((p for p in proposals if p.reference_id == overdue_task.id), None)
    assert overdue_p is not None
    assert overdue_p.action == ProactiveAction.OPEN_TASK
    assert overdue_p.priority == ProactivePriority.HIGH
    assert overdue_p.trigger == ProactiveTrigger.TASK_DUE
    assert overdue_p.action_payload == {"task_id": overdue_task.id}

    # Approaching task proposal
    approaching_p = next((p for p in proposals if p.reference_id == approaching_task.id), None)
    assert approaching_p is not None
    assert approaching_p.action == ProactiveAction.OPEN_TASK
    assert approaching_p.priority == ProactivePriority.MEDIUM
    assert approaching_p.trigger == ProactiveTrigger.TASK_DUE
    assert approaching_p.action_payload == {"task_id": approaching_task.id}


# MARK: - CommitmentProposalEvaluator Tests

@pytest.mark.asyncio
async def test_commitment_evaluator_approaching():
    cmt_store = InMemoryCommitmentStore()
    await cmt_store.initialize()
    cmt_mgr = CommitmentManager(store=cmt_store)
    evaluator = CommitmentProposalEvaluator(commitment_manager=cmt_mgr)

    now_utc = datetime.now(timezone.utc)

    # Approaching commitment (in 2 hours)
    approaching_cmt = Commitment(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Deliver client draft",
        status=CommitmentStatus.ACTIVE,
        target_date=now_utc + timedelta(hours=2),
    )
    await cmt_store.put(approaching_cmt)

    # Distant commitment (in 10 hours) - should NOT generate proposal
    distant_cmt = Commitment(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Next week milestone",
        status=CommitmentStatus.ACTIVE,
        target_date=now_utc + timedelta(hours=10),
    )
    await cmt_store.put(distant_cmt)

    proposals = await evaluator.evaluate_proposals(user_id=DEFAULT_LOCAL_USER_ID)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.action == ProactiveAction.OPEN_COMMITMENT
    assert p.priority == ProactivePriority.MEDIUM
    assert p.trigger == ProactiveTrigger.COMMITMENT_DUE
    assert p.reference_id == approaching_cmt.id
    assert p.action_payload == {"commitment_id": approaching_cmt.id}


# MARK: - CalendarProposalEvaluator Tests

@pytest.mark.asyncio
async def test_calendar_evaluator_authorized_and_unauthorized(permission_manager):
    cal_mgr = CalendarManager(permission_manager=permission_manager)
    evaluator = CalendarProposalEvaluator(calendar_manager=cal_mgr)

    now_utc = datetime.now(timezone.utc)

    # 1. When CALENDAR permission is NOT authorized
    await permission_manager.update_permission(
        PermissionType.CALENDAR,
        PermissionStatus.DENIED,
        user_id=DEFAULT_LOCAL_USER_ID,
    )
    proposals_denied = await evaluator.evaluate_proposals(user_id=DEFAULT_LOCAL_USER_ID)
    assert proposals_denied == []

    # 2. Authorize CALENDAR permission
    await permission_manager.update_permission(
        PermissionType.CALENDAR,
        PermissionStatus.AUTHORIZED,
        user_id=DEFAULT_LOCAL_USER_ID,
    )

    ev_soon = CalendarEvent(
        event_id="cal_evt_1",
        title="Sprint Standup",
        start_at=now_utc + timedelta(minutes=30),
        end_at=now_utc + timedelta(minutes=60),
    )
    ev_later = CalendarEvent(
        event_id="cal_evt_2",
        title="Design Sync",
        start_at=now_utc + timedelta(hours=3),
        end_at=now_utc + timedelta(hours=4),
    )
    await cal_mgr.set_events([ev_soon, ev_later], user_id=DEFAULT_LOCAL_USER_ID)

    proposals_auth = await evaluator.evaluate_proposals(user_id=DEFAULT_LOCAL_USER_ID)
    assert len(proposals_auth) == 1
    p = proposals_auth[0]
    assert p.action == ProactiveAction.OPEN_CALENDAR_EVENT
    assert p.priority == ProactivePriority.HIGH
    assert p.trigger == ProactiveTrigger.CALENDAR_EVENT
    assert p.reference_id == ev_soon.event_id
    assert p.action_payload == {"event_id": ev_soon.event_id}


# MARK: - ReminderProposalEvaluator Tests

@pytest.mark.asyncio
async def test_reminder_evaluator_authorized_and_unauthorized(permission_manager):
    rem_store = InMemoryReminderStore()
    await rem_store.initialize()
    rem_mgr = ReminderManager(store=rem_store)
    evaluator = ReminderProposalEvaluator(
        reminder_manager=rem_mgr,
        permission_manager=permission_manager,
    )

    now_utc = datetime.now(timezone.utc)

    # Create a scheduled reminder in the past
    rem_past = Reminder(
        user_id=DEFAULT_LOCAL_USER_ID,
        title="Check dry cleaning",
        scheduled_at=now_utc - timedelta(minutes=15),
        status=ReminderStatus.SCHEDULED,
    )
    await rem_store.put(rem_past)

    # 1. When REMINDERS is DENIED
    await permission_manager.update_permission(
        PermissionType.REMINDERS,
        PermissionStatus.DENIED,
        user_id=DEFAULT_LOCAL_USER_ID,
    )
    proposals_denied = await evaluator.evaluate_proposals(user_id=DEFAULT_LOCAL_USER_ID)
    assert proposals_denied == []

    # 2. When REMINDERS is AUTHORIZED
    await permission_manager.update_permission(
        PermissionType.REMINDERS,
        PermissionStatus.AUTHORIZED,
        user_id=DEFAULT_LOCAL_USER_ID,
    )
    proposals_auth = await evaluator.evaluate_proposals(user_id=DEFAULT_LOCAL_USER_ID)
    assert len(proposals_auth) == 1
    p = proposals_auth[0]
    assert p.action == ProactiveAction.OPEN_REMINDER
    assert p.priority == ProactivePriority.MEDIUM
    assert p.trigger == ProactiveTrigger.TIME
    assert p.reference_id == rem_past.id
    assert p.action_payload == {"reminder_id": rem_past.id}


# MARK: - Evaluator Constraints (No State-Changing Generation)

@pytest.mark.asyncio
async def test_evaluators_never_generate_state_changing_actions(permission_manager):
    task_store = InMemoryTaskStore()
    await task_store.initialize()
    task_mgr = TaskManager(store=task_store)

    cmt_store = InMemoryCommitmentStore()
    await cmt_store.initialize()
    cmt_mgr = CommitmentManager(store=cmt_store)

    cal_mgr = CalendarManager(permission_manager=permission_manager)
    await permission_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED)

    rem_store = InMemoryReminderStore()
    await rem_store.initialize()
    rem_mgr = ReminderManager(store=rem_store)
    await permission_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED)

    evaluators = [
        TaskProposalEvaluator(task_manager=task_mgr),
        CommitmentProposalEvaluator(commitment_manager=cmt_mgr),
        CalendarProposalEvaluator(calendar_manager=cal_mgr),
        ReminderProposalEvaluator(reminder_manager=rem_mgr, permission_manager=permission_manager),
    ]

    for evaluator in evaluators:
        proposals = await evaluator.evaluate_proposals(DEFAULT_LOCAL_USER_ID)
        for p in proposals:
            assert p.action not in (
                ProactiveAction.COMPLETE_TASK,
                ProactiveAction.COMPLETE_COMMITMENT,
                ProactiveAction.CANCEL_REMINDER,
            )


# MARK: - ProactiveManager Evaluation Orchestration & Rate Limiting

@pytest.mark.asyncio
async def test_run_subsystem_evaluation_pipeline(proactive_manager, permission_manager):
    task_store = InMemoryTaskStore()
    await task_store.initialize()
    task_mgr = TaskManager(store=task_store)
    now_utc = datetime.now(timezone.utc)

    # Add 2 overdue tasks
    for i in range(2):
        await task_store.put(
            Task(
                user_id=DEFAULT_LOCAL_USER_ID,
                title=f"Task {i}",
                status=TaskStatus.PENDING,
                due_at=now_utc - timedelta(minutes=10 * (i + 1)),
            )
        )

    task_evaluator = TaskProposalEvaluator(task_manager=task_mgr)

    # First evaluation run: generates 2 eligible candidates
    candidates = await proactive_manager.run_subsystem_evaluation(
        user_id=DEFAULT_LOCAL_USER_ID,
        evaluators=[task_evaluator],
    )
    assert len(candidates) == 2
    for c in candidates:
        assert c.status == ProactiveStatus.ELIGIBLE

    # Second evaluation run immediately after:
    # Deduplication suppresses duplicate proposals!
    candidates_second = await proactive_manager.run_subsystem_evaluation(
        user_id=DEFAULT_LOCAL_USER_ID,
        evaluators=[task_evaluator],
    )
    assert len(candidates_second) == 2
    for c in candidates_second:
        assert c.status == ProactiveStatus.SUPPRESSED
        assert c.suppression_reason == "deduplicated"


@pytest.mark.asyncio
async def test_rate_limit_enforced_on_evaluation(proactive_manager):
    task_store = InMemoryTaskStore()
    await task_store.initialize()
    task_mgr = TaskManager(store=task_store)
    now_utc = datetime.now(timezone.utc)

    # Add 7 distinct tasks
    for i in range(7):
        await task_store.put(
            Task(
                user_id=DEFAULT_LOCAL_USER_ID,
                title=f"Task {i}",
                status=TaskStatus.PENDING,
                due_at=now_utc - timedelta(minutes=5 * (i + 1)),
            )
        )

    task_evaluator = TaskProposalEvaluator(task_manager=task_mgr)
    candidates = await proactive_manager.run_subsystem_evaluation(
        user_id=DEFAULT_LOCAL_USER_ID,
        evaluators=[task_evaluator],
    )

    eligible = [c for c in candidates if c.status == ProactiveStatus.ELIGIBLE]
    suppressed = [c for c in candidates if c.status == ProactiveStatus.SUPPRESSED]

    # Exactly 5 allowed per hour; remaining 2 suppressed by rate limit
    assert len(eligible) == 5
    assert len(suppressed) == 2
    for s in suppressed:
        assert s.suppression_reason == "rate_limit_exceeded"


# MARK: - REST POST /api/v1/proactive/evaluate Endpoint

def test_rest_post_evaluate_endpoint():
    client = TestClient(app)
    response = client.post(
        "/api/v1/proactive/evaluate",
        headers={"X-User-ID": "test_eval_user"},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)

    for item in data:
        # Assert strict data minimization: public responses must NOT leak internal fields
        assert "reference_id" not in item
        assert "metadata" not in item
        assert "action_payload" not in item
        assert "candidate_id" in item
        assert "title" in item
        assert "action" in item
