import pytest
import os
import uuid
from datetime import datetime, timezone, timedelta

from app.services.reminders.models import Reminder, ReminderStatus
from app.services.reminders.store import InMemoryReminderStore, SQLiteReminderStore
from app.services.reminders.manager import ReminderManager
from app.services.tasks import TaskManager, InMemoryTaskStore
from app.services.commitments import CommitmentManager, InMemoryCommitmentStore


@pytest.mark.asyncio
async def test_reminder_model_creation_and_utc_validation():
    now_utc = datetime.now(timezone.utc)
    rem = Reminder(
        title="Dentist Appointment",
        scheduled_at=now_utc + timedelta(hours=2),
        body="Dr. Smith at 3 PM",
    )
    assert rem.title == "Dentist Appointment"
    assert rem.status == ReminderStatus.SCHEDULED
    assert rem.is_scheduled is True
    assert rem.is_active is True
    assert rem.scheduled_at.tzinfo == timezone.utc

    # Timezone naive datetime should be converted to UTC
    naive_dt = datetime(2026, 10, 1, 12, 0, 0)
    rem2 = Reminder(
        title="Check Mail",
        scheduled_at=naive_dt,
    )
    assert rem2.scheduled_at.tzinfo == timezone.utc
    assert rem2.scheduled_at.year == 2026


@pytest.mark.asyncio
async def test_in_memory_reminder_store_crud():
    store = InMemoryReminderStore()
    await store.initialize()

    user_a = "usr_alice"
    scheduled_time = datetime.now(timezone.utc) + timedelta(days=1)
    reminder = Reminder(
        title="Take Medicine",
        body="Two pills after dinner",
        scheduled_at=scheduled_time,
        user_id=user_a,
    )

    saved = await store.put(reminder)
    assert saved.id == reminder.id

    fetched = await store.get(reminder.id, user_a)
    assert fetched is not None
    assert fetched.title == "Take Medicine"

    # User isolation: user B cannot access user A's reminder
    fetched_b = await store.get(reminder.id, "usr_bob")
    assert fetched_b is None

    # List & count
    items = await store.list_reminders(user_a)
    assert len(items) == 1
    count = await store.count(user_a)
    assert count == 1

    # Soft delete
    deleted = await store.delete(reminder.id, user_a, soft=True)
    assert deleted is True

    # Inactive excluded by default
    active_items = await store.list_reminders(user_a, include_inactive=False)
    assert len(active_items) == 0
    all_items = await store.list_reminders(user_a, include_inactive=True)
    assert len(all_items) == 1


@pytest.mark.asyncio
async def test_sqlite_reminder_store(tmp_path):
    db_file = str(tmp_path / "test_aurora.db")
    store = SQLiteReminderStore(db_path=db_file)
    await store.initialize()

    user_id = "usr_test_sqlite"
    scheduled = datetime.now(timezone.utc) + timedelta(hours=5)
    r1 = Reminder(title="SQLite Reminder 1", scheduled_at=scheduled, user_id=user_id)
    r2 = Reminder(title="SQLite Reminder 2", scheduled_at=scheduled + timedelta(hours=1), user_id=user_id)

    await store.put(r1)
    await store.put(r2)

    fetched = await store.get(r1.id, user_id)
    assert fetched is not None
    assert fetched.title == "SQLite Reminder 1"
    assert fetched.status == ReminderStatus.SCHEDULED

    # List & filter
    listed = await store.list_reminders(user_id=user_id, limit=10)
    assert len(listed) == 2
    assert listed[0].id == r1.id

    # Count
    total = await store.count(user_id=user_id)
    assert total == 2

    # Query search
    searched = await store.list_reminders(user_id=user_id, query="Reminder 2")
    assert len(searched) == 1
    assert searched[0].title == "SQLite Reminder 2"

    # Soft delete
    del_res = await store.delete(r1.id, user_id=user_id, soft=True)
    assert del_res is True

    listed_after = await store.list_reminders(user_id=user_id, include_inactive=False)
    assert len(listed_after) == 1
    assert listed_after[0].id == r2.id


@pytest.mark.asyncio
async def test_reminder_manager_task_commitment_validation():
    task_store = InMemoryTaskStore()
    await task_store.initialize()
    task_mgr = TaskManager(store=task_store)

    cmt_store = InMemoryCommitmentStore()
    await cmt_store.initialize()
    cmt_mgr = CommitmentManager(store=cmt_store)

    rem_store = InMemoryReminderStore()
    await rem_store.initialize()
    rem_mgr = ReminderManager(store=rem_store, task_manager=task_mgr, commitment_manager=cmt_mgr)

    user_a = "usr_alice"
    user_b = "usr_bob"

    # User A creates a task
    task_a = await task_mgr.create_task(title="Write Docs", user_id=user_a)
    # User B creates a task
    task_b = await task_mgr.create_task(title="Fix Bug", user_id=user_b)
    # User A creates a commitment
    cmt_a = await cmt_mgr.create_commitment(title="Ship v1", user_id=user_a)

    target_dt = datetime.now(timezone.utc) + timedelta(hours=3)

    # 1. Valid reminder linked to User A's task
    rem1 = await rem_mgr.create_reminder(
        title="Remind to write docs",
        scheduled_at=target_dt,
        user_id=user_a,
        task_id=task_a.id,
    )
    assert rem1.task_id == task_a.id
    assert rem1.user_id == user_a

    # 2. Denial: User A tries to link User B's task
    with pytest.raises(ValueError, match="does not exist or does not belong to user"):
        await rem_mgr.create_reminder(
            title="Try linking Bob's task",
            scheduled_at=target_dt,
            user_id=user_a,
            task_id=task_b.id,
        )

    # 3. Valid reminder linked to User A's commitment
    rem2 = await rem_mgr.create_reminder(
        title="Remind to ship v1",
        scheduled_at=target_dt,
        user_id=user_a,
        commitment_id=cmt_a.id,
    )
    assert rem2.commitment_id == cmt_a.id

    # 4. Denial: Cannot link both task and commitment simultaneously
    with pytest.raises(ValueError, match="cannot reference both a task and a commitment"):
        await rem_mgr.create_reminder(
            title="Both task and commitment",
            scheduled_at=target_dt,
            user_id=user_a,
            task_id=task_a.id,
            commitment_id=cmt_a.id,
        )

    # 5. Independent reminder without task or commitment
    rem3 = await rem_mgr.create_reminder(
        title="Buy Groceries",
        scheduled_at=target_dt,
        user_id=user_a,
    )
    assert rem3.task_id is None
    assert rem3.commitment_id is None


@pytest.mark.asyncio
async def test_reminder_lifecycle_and_update():
    store = InMemoryReminderStore()
    await store.initialize()
    manager = ReminderManager(store=store)

    user_id = "usr_lifecycle"
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=2)

    rem = await manager.create_reminder(
        title="Submit Taxes",
        scheduled_at=scheduled_at,
        user_id=user_id,
        body="Remember receipts",
    )
    assert rem.status == ReminderStatus.SCHEDULED

    # Update title and body
    updated = await manager.update_reminder(
        reminder_id=rem.id,
        user_id=user_id,
        title="Submit Taxes 2026",
        body="Remember all receipts and 1099s",
    )
    assert updated.title == "Submit Taxes 2026"
    assert updated.body == "Remember all receipts and 1099s"

    # Cancel reminder
    cancelled = await manager.cancel_reminder(reminder_id=rem.id, user_id=user_id)
    assert cancelled.status == ReminderStatus.CANCELLED
    assert cancelled.is_cancelled is True
    assert cancelled.is_active is True

    # Trying to update a cancelled reminder raises ValueError
    with pytest.raises(ValueError, match="Cannot update a reminder with status 'cancelled'"):
        await manager.update_reminder(reminder_id=rem.id, user_id=user_id, title="New Title")
