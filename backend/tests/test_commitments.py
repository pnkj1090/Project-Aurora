import pytest
import os
import tempfile
from datetime import datetime, timezone, timedelta

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.commitments import (
    Commitment,
    CommitmentStatus,
    CommitmentPriority,
    InMemoryCommitmentStore,
    SQLiteCommitmentStore,
    CommitmentManager,
)


@pytest.mark.asyncio
async def test_in_memory_commitment_store():
    store = InMemoryCommitmentStore()
    await store.initialize()

    c1 = Commitment(
        user_id="user_1",
        title="Promise to send report by Friday",
        priority=CommitmentPriority.HIGH,
        target_date=datetime.now(timezone.utc) + timedelta(days=3)
    )
    saved = await store.put(c1)
    assert saved.id == c1.id

    fetched = await store.get(c1.id, user_id="user_1")
    assert fetched is not None
    assert fetched.title == "Promise to send report by Friday"
    assert fetched.priority == CommitmentPriority.HIGH

    # User isolation
    assert await store.get(c1.id, user_id="user_2") is None

    # List & count
    items = await store.list_commitments(user_id="user_1")
    assert len(items) == 1
    assert await store.count(user_id="user_1") == 1
    assert await store.count(user_id="user_2") == 0

    # Soft delete
    deleted = await store.delete(c1.id, user_id="user_1", soft=True)
    assert deleted is True
    assert (await store.get(c1.id, user_id="user_1")).is_active is False
    assert len(await store.list_commitments(user_id="user_1", include_inactive=False)) == 0
    assert len(await store.list_commitments(user_id="user_1", include_inactive=True)) == 1


@pytest.mark.asyncio
async def test_sqlite_commitment_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_commitments.db")
        store = SQLiteCommitmentStore(db_path=db_path)
        await store.initialize()

        c1 = Commitment(
            user_id="user_y",
            title="Follow up with architect",
            description="Discuss blueprints",
            priority=CommitmentPriority.MEDIUM,
            tags=["house", "architecture"]
        )
        await store.put(c1)

        fetched = await store.get(c1.id, user_id="user_y")
        assert fetched is not None
        assert fetched.title == "Follow up with architect"
        assert fetched.tags == ["house", "architecture"]

        # Search query
        results = await store.list_commitments(user_id="user_y", query="blueprints")
        assert len(results) == 1
        assert results[0].id == c1.id

        no_results = await store.list_commitments(user_id="user_y", query="cars")
        assert len(no_results) == 0

        # Hard delete
        deleted = await store.delete(c1.id, user_id="user_y", soft=False)
        assert deleted is True
        assert await store.get(c1.id, user_id="user_y") is None


@pytest.mark.asyncio
async def test_commitment_manager_lifecycle_and_deletion():
    store = InMemoryCommitmentStore()
    manager = CommitmentManager(store=store)

    # 1. Create commitment
    cmt = await manager.create_commitment(
        title="Check in on team deliverables",
        description="Weekly cadence",
        priority=CommitmentPriority.HIGH
    )
    assert cmt.status == CommitmentStatus.ACTIVE
    assert cmt.is_active is True
    assert cmt.completed_at is None

    # 2. Update commitment
    updated = await manager.update_commitment(
        commitment_id=cmt.id,
        title="Check in on team sprint deliverables",
        priority=CommitmentPriority.MEDIUM
    )
    assert updated.title == "Check in on team sprint deliverables"
    assert updated.priority == CommitmentPriority.MEDIUM

    # 3. Complete commitment (does NOT soft-delete)
    completed = await manager.complete_commitment(cmt.id)
    assert completed.status == CommitmentStatus.COMPLETED
    assert completed.completed_at is not None
    assert completed.is_active is True  # Distinct from deletion!

    # Idempotent complete
    completed_again = await manager.complete_commitment(cmt.id)
    assert completed_again.status == CommitmentStatus.COMPLETED

    # Cannot cancel completed commitment
    with pytest.raises(ValueError, match="Cannot cancel completed commitment"):
        await manager.cancel_commitment(cmt.id)

    # 4. New commitment to test cancellation
    cmt2 = await manager.create_commitment(title="Obsolete commitment")
    cancelled = await manager.cancel_commitment(cmt2.id)
    assert cancelled.status == CommitmentStatus.CANCELLED
    assert cancelled.is_active is True  # Distinct from deletion!

    # Cannot complete cancelled commitment
    with pytest.raises(ValueError, match="Cannot complete cancelled commitment"):
        await manager.complete_commitment(cmt2.id)

    # 5. Soft delete (does NOT mark completed or cancelled)
    del_res = await manager.delete_commitment(cmt.id, soft=True)
    assert del_res is True
    deleted_item = await manager.get_commitment(cmt.id)
    assert deleted_item.is_active is False
    assert deleted_item.status == CommitmentStatus.COMPLETED  # Status preserved!

    # Updating inactive commitment raises error
    with pytest.raises(ValueError, match="Cannot update inactive"):
        await manager.update_commitment(cmt.id, title="Try to update")
