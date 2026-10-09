import asyncio
import os
import tempfile
import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.permissions.models import PermissionType, PermissionStatus
from app.services.permissions.manager import PermissionManager, InMemoryPermissionStore, get_permission_manager_dependency
from app.services.tasks.models import Task, TaskStatus, TaskPriority
from app.services.tasks.store import InMemoryTaskStore
from app.services.tasks import TaskManager, get_task_manager_dependency
from app.services.commitments.models import Commitment, CommitmentStatus
from app.services.commitments.store import InMemoryCommitmentStore
from app.services.commitments import CommitmentManager, get_commitment_manager_dependency
from app.services.reminders.models import Reminder, ReminderStatus
from app.services.reminders.store import SQLiteReminderStore, InMemoryReminderStore
from app.services.reminders.manager import ReminderManager, get_reminder_manager_dependency
from app.services.calendar.models import CalendarEvent
from app.services.calendar.manager import CalendarManager
from app.services.tools.base import ToolRiskLevel
from app.services.tools.schemas import ToolCall
from app.services.tools.registry import ToolRegistry
from app.services.tools.executor import ToolExecutor, ToolConfirmationRequired
from app.services.tools.builtins.reminder_tool import (
    CreateReminderTool,
    UpdateReminderTool,
    SnoozeReminderTool,
    CancelReminderTool,
    LinkReminderToItemTool,
    UnlinkReminderFromItemTool,
    build_client_action,
)
from app.services.tools.builtins.calendar_tool import AnalyzeScheduleAvailabilityTool
from app.services.conversation.models import AgentExecutionState, AgentExecutionStatus
from app.services.conversation.agent_loop import deduplicate_client_actions


# MARK: - Fixtures

@pytest.fixture
def temp_sqlite_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = SQLiteReminderStore(db_path=path)
    yield store, path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def setup_environment():
    perm_store = InMemoryPermissionStore()
    perm_manager = PermissionManager(store=perm_store)

    task_store = InMemoryTaskStore()
    task_manager = TaskManager(store=task_store)

    com_store = InMemoryCommitmentStore()
    com_manager = CommitmentManager(store=com_store)

    rem_store = InMemoryReminderStore()
    rem_manager = ReminderManager(
        store=rem_store,
        task_manager=task_manager,
        commitment_manager=com_manager
    )

    cal_manager = CalendarManager(permission_manager=perm_manager)

    return {
        "perm_manager": perm_manager,
        "task_manager": task_manager,
        "com_manager": com_manager,
        "rem_manager": rem_manager,
        "cal_manager": cal_manager,
    }


# MARK: - Category A: Direct REST Identity

@pytest.mark.asyncio
async def test_a1_direct_rest_missing_identity_fails_closed(setup_environment):
    env = setup_environment
    app.dependency_overrides[get_reminder_manager_dependency] = lambda: env["rem_manager"]
    app.dependency_overrides[get_permission_manager_dependency] = lambda: env["perm_manager"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No X-User-ID header
        sched_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        resp = await client.post(
            "/api/v1/reminders",
            json={"title": "No Auth", "scheduled_at": sched_time}
        )
        assert resp.status_code == 401
        assert "Missing or invalid authentication" in resp.json()["detail"]

        # Whitespace X-User-ID header
        resp_ws = await client.get("/api/v1/reminders", headers={"X-User-ID": "   "})
        assert resp_ws.status_code == 401

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a2_direct_rest_cross_user_isolation(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    perm_manager: PermissionManager = env["perm_manager"]

    user_a = "usr_alice"
    user_b = "usr_bob"

    # Authorize both users for reminders
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=user_a)
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=user_b)

    rem = await rem_manager.create_reminder(
        title="Alice's Secret Reminder",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        user_id=user_a
    )

    app.dependency_overrides[get_reminder_manager_dependency] = lambda: rem_manager
    app.dependency_overrides[get_permission_manager_dependency] = lambda: perm_manager

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # User B attempts to access Alice's reminder -> 404
        get_b = await client.get(f"/api/v1/reminders/{rem.id}", headers={"X-User-ID": user_b})
        assert get_b.status_code == 404

        # User B attempts to patch Alice's reminder -> 404
        patch_b = await client.patch(
            f"/api/v1/reminders/{rem.id}",
            headers={"X-User-ID": user_b},
            json={"title": "Hacked Title"}
        )
        assert patch_b.status_code == 404

        # User B attempts to snooze Alice's reminder -> 404
        snooze_b = await client.post(
            f"/api/v1/reminders/{rem.id}/snooze",
            headers={"X-User-ID": user_b},
            json={"duration_minutes": 30}
        )
        assert snooze_b.status_code == 404

        # User B attempts to cancel Alice's reminder -> 404
        cancel_b = await client.post(
            f"/api/v1/reminders/{rem.id}/cancel",
            headers={"X-User-ID": user_b}
        )
        assert cancel_b.status_code == 404

    app.dependency_overrides.clear()


# MARK: - Category B: Direct REST Permissions

@pytest.mark.asyncio
async def test_b1_direct_rest_permission_enforcement(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    perm_manager: PermissionManager = env["perm_manager"]

    user_unauth = "usr_unauthorized"
    user_auth = "usr_authorized"

    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=user_unauth)
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=user_auth)

    app.dependency_overrides[get_reminder_manager_dependency] = lambda: rem_manager
    app.dependency_overrides[get_permission_manager_dependency] = lambda: perm_manager

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        sched_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        # Unauthorized user receives 403 on create
        resp_unauth = await client.post(
            "/api/v1/reminders",
            headers={"X-User-ID": user_unauth},
            json={"title": "Doctor", "scheduled_at": sched_time}
        )
        assert resp_unauth.status_code == 403
        assert "not authorized" in resp_unauth.json()["detail"]

        # Unauthorized user receives 403 on list
        list_unauth = await client.get("/api/v1/reminders", headers={"X-User-ID": user_unauth})
        assert list_unauth.status_code == 403

        # Authorized user succeeds on create
        resp_auth = await client.post(
            "/api/v1/reminders",
            headers={"X-User-ID": user_auth},
            json={"title": "Doctor", "scheduled_at": sched_time}
        )
        assert resp_auth.status_code == 201
        created_id = resp_auth.json()["id"]

        # Authorized user succeeds on list
        list_auth = await client.get("/api/v1/reminders", headers={"X-User-ID": user_auth})
        assert list_auth.status_code == 200
        assert list_auth.json()["total"] == 1

    app.dependency_overrides.clear()


# MARK: - Category C: SQLite Concurrency & Canonical Action IDs

@pytest.mark.asyncio
async def test_c1_sqlite_concurrent_mutations_and_monotonic_updated_at(temp_sqlite_store):
    store, _ = temp_sqlite_store
    await store.initialize()

    user_id = "usr_concurrency"
    target_dt = datetime.now(timezone.utc) + timedelta(hours=5)

    # Initial creation
    initial = Reminder(
        user_id=user_id,
        title="Initial Reminder",
        body="Initial Body",
        scheduled_at=target_dt,
        status=ReminderStatus.SCHEDULED,
    )
    saved, mutated = await store.save_mutation(initial)
    assert mutated is True
    initial_updated_at = saved.updated_at
    rem_id = saved.id

    # Execute 10 concurrent mutation tasks on the same reminder
    async def mutate_task(index: int):
        await asyncio.sleep(0.01 * (index % 3))
        # Fetch current
        curr = await store.get(reminder_id=rem_id, user_id=user_id)
        assert curr is not None
        curr.title = f"Updated Title {index}"
        curr.body = f"Updated Body {index}"
        curr.scheduled_at = target_dt + timedelta(minutes=index)
        return await store.save_mutation(curr)

    results = await asyncio.gather(*(mutate_task(i) for i in range(10)))

    # All mutations must report mutated=True
    for updated_rem, mut in results:
        assert mut is True
        assert updated_rem.updated_at > initial_updated_at

    # Check final state in SQLite
    final = await store.get(reminder_id=rem_id, user_id=user_id)
    assert final is not None
    assert final.updated_at > initial_updated_at


@pytest.mark.asyncio
async def test_c2_canonical_action_id_format_and_deterministic_replay():
    now_utc = datetime(2026, 10, 15, 12, 30, 45, 123456, tzinfo=timezone.utc)
    sched_utc = datetime(2026, 10, 15, 15, 0, 0, 0, tzinfo=timezone.utc)

    rem = Reminder(
        id="rem_deterministic_test",
        user_id="usr_det",
        title="Review PR",
        body="Check comments",
        scheduled_at=sched_utc,
        updated_at=now_utc,
        status=ReminderStatus.SCHEDULED,
    )

    action1 = build_client_action("schedule_local_notification", rem)
    action2 = build_client_action("schedule_local_notification", rem)

    # Identical state produces identical action ID
    expected_id = "schedule_local_notification:rem_deterministic_test:2026-10-15T12:30:45.123456Z"
    assert action1["action_id"] == expected_id
    assert action2["action_id"] == expected_id

    # Distinct mutation produces distinct action ID
    rem_mutated = rem.model_copy()
    rem_mutated.updated_at = datetime(2026, 10, 15, 12, 30, 45, 123457, tzinfo=timezone.utc)
    action3 = build_client_action("reschedule_local_notification", rem_mutated)
    assert action3["action_id"] != action1["action_id"]
    assert action3["action_id"] == "reschedule_local_notification:rem_deterministic_test:2026-10-15T12:30:45.123457Z"


# MARK: - Category D: Strict No-Op Semantics

@pytest.mark.asyncio
async def test_d1_identical_update_is_strict_noop(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    user_id = "usr_noop_test"

    sched_dt = datetime.now(timezone.utc) + timedelta(hours=3)
    created = await rem_manager.create_reminder(
        title="Buy Groceries",
        body="Apples and milk",
        scheduled_at=sched_dt,
        user_id=user_id,
    )
    created_updated_at = created.updated_at

    # Update with identical parameters
    updated, mutated = await rem_manager.update_reminder_with_status(
        reminder_id=created.id,
        user_id=user_id,
        title="Buy Groceries",
        body="Apples and milk",
        scheduled_at=sched_dt,
    )

    assert mutated is False
    assert updated.updated_at == created_updated_at

    # Via UpdateReminderTool: emits no client action
    update_tool = UpdateReminderTool(reminder_manager=rem_manager)
    tool_res = await update_tool.execute({
        "_user_id": user_id,
        "reminder_id": created.id,
        "title": "Buy Groceries",
        "body": "Apples and milk",
        "scheduled_at": sched_dt.isoformat(),
    })
    assert tool_res["status"] == "success"
    assert tool_res["mutated"] is False
    assert tool_res["client_action"] is None


@pytest.mark.asyncio
async def test_d2_already_cancelled_and_already_unlinked_are_noops(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    user_id = "usr_noop_ops"

    created = await rem_manager.create_reminder(
        title="Meeting",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        user_id=user_id,
    )

    # 1. First cancel -> mutated=True
    c1, mut1 = await rem_manager.cancel_reminder_with_status(created.id, user_id=user_id)
    assert mut1 is True
    assert c1.status == ReminderStatus.CANCELLED
    updated_at_after_cancel = c1.updated_at

    # 2. Second cancel -> mutated=False, updated_at preserved
    c2, mut2 = await rem_manager.cancel_reminder_with_status(created.id, user_id=user_id)
    assert mut2 is False
    assert c2.updated_at == updated_at_after_cancel

    # Via CancelReminderTool: second cancel produces no client action
    cancel_tool = CancelReminderTool(reminder_manager=rem_manager)
    tool_res = await cancel_tool.execute({"_user_id": user_id, "reminder_id": created.id})
    assert tool_res["status"] == "success"
    assert tool_res["mutated"] is False
    assert tool_res["client_action"] is None

    # 3. Unlink when no link exists -> mutated=False, updated_at preserved
    unlink_tool = UnlinkReminderFromItemTool(reminder_manager=rem_manager)
    unlink_res = await unlink_tool.execute({"_user_id": user_id, "reminder_id": created.id})
    assert unlink_res["status"] == "success"
    assert unlink_res["mutated"] is False
    assert unlink_res["client_action"] is None


# MARK: - Category E: Notification Synchronization

@pytest.mark.asyncio
async def test_e1_direct_rest_notification_sync_signals(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    perm_manager: PermissionManager = env["perm_manager"]
    user_id = "usr_sync_signals"

    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=user_id)

    app.dependency_overrides[get_reminder_manager_dependency] = lambda: rem_manager
    app.dependency_overrides[get_permission_manager_dependency] = lambda: perm_manager

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-User-ID": user_id}
        sched_time = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()

        # 1. Create -> SCHEDULE
        c_res = await client.post(
            "/api/v1/reminders",
            headers=headers,
            json={"title": "Flight Check-in", "scheduled_at": sched_time}
        )
        assert c_res.status_code == 201
        rem_id = c_res.json()["id"]
        assert c_res.json()["notification_sync"] == "SCHEDULE"

        # 2. Title-only patch -> RESCHEDULE
        p_title = await client.patch(
            f"/api/v1/reminders/{rem_id}",
            headers=headers,
            json={"title": "Flight Check-in (Terminal 2)"}
        )
        assert p_title.status_code == 200
        assert p_title.json()["notification_sync"] == "RESCHEDULE"

        # 3. Body-only patch -> RESCHEDULE
        p_body = await client.patch(
            f"/api/v1/reminders/{rem_id}",
            headers=headers,
            json={"body": "Gate B12"}
        )
        assert p_body.status_code == 200
        assert p_body.json()["notification_sync"] == "RESCHEDULE"

        # 4. Snooze -> RESCHEDULE
        sn_res = await client.post(
            f"/api/v1/reminders/{rem_id}/snooze",
            headers=headers,
            json={"duration_minutes": 20}
        )
        assert sn_res.status_code == 200
        assert sn_res.json()["notification_sync"] == "RESCHEDULE"

        # 5. Cancel -> CANCEL
        can_res = await client.post(f"/api/v1/reminders/{rem_id}/cancel", headers=headers)
        assert can_res.status_code == 200
        assert can_res.json()["notification_sync"] == "CANCEL"

        # 6. Already cancelled cancel -> NONE
        can2_res = await client.post(f"/api/v1/reminders/{rem_id}/cancel", headers=headers)
        assert can2_res.status_code == 200
        assert can2_res.json()["notification_sync"] == "NONE"

        # 7. Delete cancelled reminder -> NONE
        del_cancelled = await client.delete(f"/api/v1/reminders/{rem_id}", headers=headers)
        assert del_cancelled.status_code == 200
        assert del_cancelled.json()["notification_sync"] == "NONE"

        # 8. Create another scheduled reminder and delete -> CANCEL
        c2 = await client.post(
            "/api/v1/reminders",
            headers=headers,
            json={"title": "To Delete", "scheduled_at": sched_time}
        )
        assert c2.status_code == 201
        rem_id2 = c2.json()["id"]
        del_scheduled = await client.delete(f"/api/v1/reminders/{rem_id2}", headers=headers)
        assert del_scheduled.status_code == 200
        assert del_scheduled.json()["notification_sync"] == "CANCEL"

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_e2_agent_loop_client_action_harvesting_and_deduplication():
    # Test deduplicate_client_actions directly
    actions = [
        # Reminder 1 created
        {
            "action_id": "schedule_local_notification:rem_1:2026-10-01T10:00:00.000000Z",
            "action": "schedule_local_notification",
            "reminder_id": "rem_1",
            "title": "Gym",
            "scheduled_at": "2026-10-01T10:00:00.000000Z",
            "updated_at": "2026-10-01T10:00:00.000000Z",
        },
        # Reminder 2 created
        {
            "action_id": "schedule_local_notification:rem_2:2026-10-01T11:00:00.000000Z",
            "action": "schedule_local_notification",
            "reminder_id": "rem_2",
            "title": "Dentist",
            "scheduled_at": "2026-10-01T11:00:00.000000Z",
            "updated_at": "2026-10-01T11:00:00.000000Z",
        },
        # Reminder 1 rescheduled (same turn)
        {
            "action_id": "reschedule_local_notification:rem_1:2026-10-01T10:05:00.000000Z",
            "action": "reschedule_local_notification",
            "reminder_id": "rem_1",
            "title": "Gym Workout",
            "scheduled_at": "2026-10-01T12:00:00.000000Z",
            "updated_at": "2026-10-01T10:05:00.000000Z",
        },
    ]

    deduped = deduplicate_client_actions(actions)
    assert len(deduped) == 2

    # Reminder 1 should retain only the final action
    r1 = next(a for a in deduped if a["reminder_id"] == "rem_1")
    assert r1["action"] == "reschedule_local_notification"
    assert r1["title"] == "Gym Workout"
    assert r1["scheduled_at"] == "2026-10-01T12:00:00.000000Z"

    # Reminder 2 retained
    r2 = next(a for a in deduped if a["reminder_id"] == "rem_2")
    assert r2["action"] == "schedule_local_notification"
    assert r2["title"] == "Dentist"


# MARK: - Category F: Schedule Availability

@pytest.mark.asyncio
async def test_f1_schedule_availability_both_authorized(setup_environment):
    env = setup_environment
    perm_manager: PermissionManager = env["perm_manager"]
    cal_manager: CalendarManager = env["cal_manager"]
    rem_manager: ReminderManager = env["rem_manager"]
    user_id = "usr_avail_both"

    await perm_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=user_id)

    # Ingest a calendar event: 10:00 to 11:00 UTC
    day = datetime(2026, 10, 20, 0, 0, 0, tzinfo=timezone.utc)
    ev_start = day.replace(hour=10)
    ev_end = day.replace(hour=11)
    await cal_manager.set_events([
        CalendarEvent(
            event_id="ev_1",
            title="Team Standup",
            start_at=ev_start,
            end_at=ev_end,
            user_id=user_id
        )
    ], user_id=user_id)

    # Ingest a scheduled reminder: 14:00 UTC
    rem_time = day.replace(hour=14)
    await rem_manager.create_reminder(
        title="Call Client",
        scheduled_at=rem_time,
        user_id=user_id
    )

    tool = AnalyzeScheduleAvailabilityTool(
        calendar_manager=cal_manager,
        reminder_manager=rem_manager,
        permission_manager=perm_manager
    )

    res = await tool.execute({
        "_user_id": user_id,
        "start_at": day.replace(hour=9).isoformat(),
        "end_at": day.replace(hour=17).isoformat(),
        "slot_duration_minutes": 30
    })

    assert res["status"] == "success"
    assert "calendar" in res["included_sources"]
    assert "reminders" in res["included_sources"]
    assert res["omitted_sources"] == []
    assert res["events_analyzed"] == 1
    assert res["reminders_analyzed"] == 1

    # Busy blocks must include both
    sources = {b["source"] for b in res["busy_blocks"]}
    assert "calendar" in sources
    assert "reminders" in sources


@pytest.mark.asyncio
async def test_f2_schedule_availability_partial_and_fail_closed(setup_environment):
    env = setup_environment
    perm_manager: PermissionManager = env["perm_manager"]
    cal_manager: CalendarManager = env["cal_manager"]
    rem_manager: ReminderManager = env["rem_manager"]
    user_id = "usr_avail_partial"

    # 1. Calendar only authorized
    await perm_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.AUTHORIZED, user_id=user_id)
    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.DENIED, user_id=user_id)

    tool = AnalyzeScheduleAvailabilityTool(
        calendar_manager=cal_manager,
        reminder_manager=rem_manager,
        permission_manager=perm_manager
    )

    day = datetime(2026, 10, 21, 0, 0, 0, tzinfo=timezone.utc)
    res_cal_only = await tool.execute({
        "_user_id": user_id,
        "start_at": day.replace(hour=9).isoformat(),
        "end_at": day.replace(hour=17).isoformat(),
    })
    assert res_cal_only["status"] == "success"
    assert res_cal_only["included_sources"] == ["calendar"]
    assert res_cal_only["omitted_sources"] == ["reminders"]

    # 2. Neither authorized -> fail closed
    await perm_manager.update_permission(PermissionType.CALENDAR, PermissionStatus.DENIED, user_id=user_id)
    res_neither = await tool.execute({
        "_user_id": user_id,
        "start_at": day.replace(hour=9).isoformat(),
        "end_at": day.replace(hour=17).isoformat(),
    })
    assert res_neither["status"] == "error"
    assert "Both are currently unauthorized" in res_neither["error"]
    assert res_neither["omitted_sources"] == ["calendar", "reminders"]


# MARK: - Category G: Task / Commitment Linking

@pytest.mark.asyncio
async def test_g1_task_and_commitment_linking_mutual_exclusivity(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    task_manager: TaskManager = env["task_manager"]
    com_manager: CommitmentManager = env["com_manager"]
    user_id = "usr_linking"

    # Create task & commitment
    task = await task_manager.create_task(title="Deploy Release", user_id=user_id)
    com = await com_manager.create_commitment(title="Send Report", user_id=user_id)
    rem = await rem_manager.create_reminder(
        title="Check status",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        user_id=user_id
    )

    # 1. Link to task
    link_tool = LinkReminderToItemTool(reminder_manager=rem_manager)
    res1 = await link_tool.execute({"_user_id": user_id, "reminder_id": rem.id, "task_id": task.id})
    assert res1["status"] == "success"
    assert res1["task_id"] == task.id
    assert res1["commitment_id"] is None
    assert res1["client_action"] is None  # Metadata-only

    # 2. Link to commitment -> switches link, clears task link
    res2 = await link_tool.execute({"_user_id": user_id, "reminder_id": rem.id, "commitment_id": com.id})
    assert res2["status"] == "success"
    assert res2["commitment_id"] == com.id
    assert res2["task_id"] is None
    assert res2["client_action"] is None

    # 3. Providing both task_id and commitment_id fails mutual exclusivity
    res3 = await link_tool.execute({
        "_user_id": user_id,
        "reminder_id": rem.id,
        "task_id": task.id,
        "commitment_id": com.id
    })
    assert res3["status"] == "error"
    assert "cannot reference both" in res3["error"]


@pytest.mark.asyncio
async def test_g2_linking_inactive_or_cross_user_fails(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    task_manager: TaskManager = env["task_manager"]
    user_a = "usr_link_a"
    user_b = "usr_link_b"

    # Task owned by user B
    task_b = await task_manager.create_task(title="Bob's Task", user_id=user_b)
    # Reminder owned by user A
    rem_a = await rem_manager.create_reminder(
        title="Alice's Reminder",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
        user_id=user_a
    )

    link_tool = LinkReminderToItemTool(reminder_manager=rem_manager)
    # Attempting to link Alice's reminder to Bob's task fails
    res = await link_tool.execute({"_user_id": user_a, "reminder_id": rem_a.id, "task_id": task_b.id})
    assert res["status"] == "error"
    assert "does not exist, is inactive, or does not belong to user" in res["error"]


# MARK: - Category H: Deterministic Snooze

@pytest.mark.asyncio
async def test_h1_deterministic_snooze_formulas_and_bounds(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    user_id = "usr_snooze_formulas"

    now = datetime.now(timezone.utc)

    # 1. Future reminder: new_scheduled_at = scheduled_at + duration
    future_time = now + timedelta(hours=2)
    rem_future = await rem_manager.create_reminder(
        title="Future",
        scheduled_at=future_time,
        user_id=user_id
    )
    snoozed_future, mut1 = await rem_manager.snooze_reminder(rem_future.id, user_id=user_id, duration_minutes=30)
    assert mut1 is True
    assert snoozed_future.scheduled_at == future_time + timedelta(minutes=30)

    # 2. Overdue reminder: new_scheduled_at = now + duration
    past_time = now - timedelta(hours=1)
    # Directly store overdue reminder
    rem_past = Reminder(
        user_id=user_id,
        title="Overdue",
        scheduled_at=past_time,
        status=ReminderStatus.SCHEDULED,
    )
    saved_past, _ = await rem_manager._store.save_mutation(rem_past)
    before_snooze = datetime.now(timezone.utc)
    snoozed_overdue, mut2 = await rem_manager.snooze_reminder(saved_past.id, user_id=user_id, duration_minutes=15)
    after_snooze = datetime.now(timezone.utc)
    assert mut2 is True
    assert before_snooze + timedelta(minutes=15) <= snoozed_overdue.scheduled_at <= after_snooze + timedelta(minutes=15)

    # 3. Delivered reminder resets to SCHEDULED
    snoozed_overdue.status = ReminderStatus.DELIVERED
    await rem_manager._store.save_mutation(snoozed_overdue)
    snoozed_delivered, mut3 = await rem_manager.snooze_reminder(snoozed_overdue.id, user_id=user_id, duration_minutes=20)
    assert mut3 is True
    assert snoozed_delivered.status == ReminderStatus.SCHEDULED

    # 4. Cancelled reminder rejects snooze
    snoozed_delivered.status = ReminderStatus.CANCELLED
    await rem_manager._store.save_mutation(snoozed_delivered)
    with pytest.raises(ValueError) as exc_info:
        await rem_manager.snooze_reminder(snoozed_delivered.id, user_id=user_id, duration_minutes=15)
    assert "Cannot snooze a cancelled reminder" in str(exc_info.value)


# MARK: - Category I: Assistant Actions & Confirmation Lifecycle

@pytest.mark.asyncio
async def test_i1_update_tool_title_only_and_body_only_reschedule(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    user_id = "usr_tool_resched"

    sched_dt = datetime.now(timezone.utc) + timedelta(hours=3)
    created = await rem_manager.create_reminder(
        title="Original Title",
        body="Original Body",
        scheduled_at=sched_dt,
        user_id=user_id,
    )

    update_tool = UpdateReminderTool(reminder_manager=rem_manager)

    # 1. Title-only update -> reschedule_local_notification
    t_res = await update_tool.execute({
        "_user_id": user_id,
        "reminder_id": created.id,
        "title": "New Title",
    })
    assert t_res["status"] == "success"
    assert t_res["mutated"] is True
    assert t_res["client_action"] is not None
    assert t_res["client_action"]["action"] == "reschedule_local_notification"
    assert t_res["client_action"]["title"] == "New Title"
    assert t_res["client_action"]["body"] == "Original Body"

    # 2. Body-only update -> reschedule_local_notification
    b_res = await update_tool.execute({
        "_user_id": user_id,
        "reminder_id": created.id,
        "body": "New Body Details",
    })
    assert b_res["status"] == "success"
    assert b_res["mutated"] is True
    assert b_res["client_action"] is not None
    assert b_res["client_action"]["action"] == "reschedule_local_notification"
    assert b_res["client_action"]["title"] == "New Title"
    assert b_res["client_action"]["body"] == "New Body Details"


@pytest.mark.asyncio
async def test_i2_confirmation_lifecycle_client_actions(setup_environment):
    env = setup_environment
    rem_manager: ReminderManager = env["rem_manager"]
    perm_manager: PermissionManager = env["perm_manager"]
    user_id = "usr_conf_client_action"

    await perm_manager.update_permission(PermissionType.REMINDERS, PermissionStatus.AUTHORIZED, user_id=user_id)

    # Create a scheduled reminder
    rem = await rem_manager.create_reminder(
        title="To Be Cancelled",
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=2),
        user_id=user_id,
    )

    # Use CancelReminderTool which has requires_confirmation=True
    tool = CancelReminderTool(reminder_manager=rem_manager)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry=registry, permission_manager=perm_manager)

    # 1. Tool execution requiring confirmation pauses with zero client actions
    with pytest.raises(ToolConfirmationRequired) as exc_info:
        await executor.execute(
            ToolCall(id="c1", name="cancel_reminder", arguments={"reminder_id": rem.id}),
            user_id=user_id,
        )
    assert exc_info.value.pending.tool_call.name == "cancel_reminder"
    # Execution state during pause has zero client actions
    state = AgentExecutionState(
        execution_id="exec_conf_test",
        conversation_id="conv_conf_test",
        user_id=user_id,
        initial_user_query="Cancel reminder",
        execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
        client_actions=[],
    )
    assert len(state.client_actions) == 0

    # 2. Confirmation rejection -> zero client actions
    state_rejected = state.model_copy(deep=True)
    state_rejected.client_actions.clear()
    state_rejected.execution_status = AgentExecutionStatus.CANCELLED
    assert len(state_rejected.client_actions) == 0

    # 3. Confirmation approval -> executes tool, produces final client action
    approved_res = await executor.execute(
        ToolCall(id="c1", name="cancel_reminder", arguments={"reminder_id": rem.id}),
        user_id=user_id,
        is_pre_approved=True,
    )
    assert approved_res.success is True
    action = approved_res.output.get("client_action")
    assert action is not None
    assert action["action"] == "cancel_local_notification"
    assert action["reminder_id"] == rem.id

    state_approved = state.model_copy(deep=True)
    state_approved.client_actions.append(action)
    state_approved.execution_status = AgentExecutionStatus.COMPLETED
    assert len(state_approved.client_actions) == 1
    assert state_approved.client_actions[0]["action"] == "cancel_local_notification"
