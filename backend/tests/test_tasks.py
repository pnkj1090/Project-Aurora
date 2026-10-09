import pytest
import os
import tempfile
from datetime import datetime, timezone, timedelta

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.tasks import (
    Task,
    TaskStatus,
    TaskPriority,
    InMemoryTaskStore,
    SQLiteTaskStore,
    TaskManager,
)


@pytest.mark.asyncio
async def test_in_memory_task_store():
    store = InMemoryTaskStore()
    await store.initialize()

    t1 = Task(
        user_id="user_a",
        title="Write report",
        priority=TaskPriority.HIGH,
        due_at=datetime.now(timezone.utc) + timedelta(days=1)
    )
    saved = await store.put(t1)
    assert saved.id == t1.id

    fetched = await store.get(t1.id, user_id="user_a")
    assert fetched is not None
    assert fetched.title == "Write report"
    assert fetched.priority == TaskPriority.HIGH

    # User isolation
    assert await store.get(t1.id, user_id="user_b") is None

    # List
    items = await store.list_tasks(user_id="user_a")
    assert len(items) == 1
    assert await store.count(user_id="user_a") == 1
    assert await store.count(user_id="user_b") == 0

    # Soft delete
    deleted = await store.delete(t1.id, user_id="user_a", soft=True)
    assert deleted is True
    assert (await store.get(t1.id, user_id="user_a")).is_active is False
    assert len(await store.list_tasks(user_id="user_a", include_inactive=False)) == 0
    assert len(await store.list_tasks(user_id="user_a", include_inactive=True)) == 1


@pytest.mark.asyncio
async def test_sqlite_task_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_tasks.db")
        store = SQLiteTaskStore(db_path=db_path)
        await store.initialize()

        t1 = Task(
            user_id="user_x",
            title="Fix bug in engine",
            description="Detailed notes",
            priority=TaskPriority.HIGH,
            tags=["backend", "bug"]
        )
        await store.put(t1)

        fetched = await store.get(t1.id, user_id="user_x")
        assert fetched is not None
        assert fetched.title == "Fix bug in engine"
        assert fetched.tags == ["backend", "bug"]

        # Search query
        results = await store.list_tasks(user_id="user_x", query="engine")
        assert len(results) == 1
        assert results[0].id == t1.id

        no_results = await store.list_tasks(user_id="user_x", query="unrelated")
        assert len(no_results) == 0

        # Hard delete
        deleted = await store.delete(t1.id, user_id="user_x", soft=False)
        assert deleted is True
        assert await store.get(t1.id, user_id="user_x") is None


@pytest.mark.asyncio
async def test_task_manager_lifecycle_and_deletion():
    store = InMemoryTaskStore()
    manager = TaskManager(store=store)

    # 1. Create task
    task = await manager.create_task(
        title="Prepare presentation",
        description="Slides for demo",
        priority=TaskPriority.HIGH
    )
    assert task.status == TaskStatus.PENDING
    assert task.is_active is True
    assert task.completed_at is None

    # 2. Update task
    updated = await manager.update_task(
        task_id=task.id,
        title="Prepare final presentation",
        priority=TaskPriority.MEDIUM
    )
    assert updated.title == "Prepare final presentation"
    assert updated.priority == TaskPriority.MEDIUM

    # 3. Complete task (does NOT soft-delete)
    completed = await manager.complete_task(task.id)
    assert completed.status == TaskStatus.COMPLETED
    assert completed.completed_at is not None
    assert completed.is_active is True  # Distinct from deletion!

    # Idempotent complete
    completed_again = await manager.complete_task(task.id)
    assert completed_again.status == TaskStatus.COMPLETED

    # Cannot cancel completed task
    with pytest.raises(ValueError, match="Cannot cancel completed task"):
        await manager.cancel_task(task.id)

    # 4. New task to test cancellation
    task2 = await manager.create_task(title="Outdated task")
    cancelled = await manager.cancel_task(task2.id)
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.is_active is True  # Distinct from deletion!

    # Cannot complete cancelled task
    with pytest.raises(ValueError, match="Cannot complete cancelled task"):
        await manager.complete_task(task2.id)

    # 5. Soft delete (does NOT mark completed or cancelled)
    del_res = await manager.delete_task(task.id, soft=True)
    assert del_res is True
    deleted_item = await manager.get_task(task.id)
    assert deleted_item.is_active is False
    assert deleted_item.status == TaskStatus.COMPLETED  # Status preserved!

    # Updating inactive task raises error
    with pytest.raises(ValueError, match="Cannot update inactive"):
        await manager.update_task(task.id, title="Try to update")
