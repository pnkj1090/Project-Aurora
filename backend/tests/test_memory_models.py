import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from app.services.memory.schemas import (
    MemoryItem,
    MemoryType,
    MemorySource,
    MemoryStatus,
)


def test_memory_item_defaults():
    item = MemoryItem(
        type=MemoryType.FACT,
        content="User lives in San Francisco."
    )
    assert item.id.startswith("mem_")
    assert item.type == MemoryType.FACT
    assert item.importance == 3
    assert item.confidence == 1.0
    assert item.source == MemorySource.USER_EXPLICIT
    assert item.status == MemoryStatus.ACTIVE
    assert item.is_active is True
    assert item.is_expired is False
    assert item.created_at.tzinfo == timezone.utc
    assert item.updated_at.tzinfo == timezone.utc


def test_memory_type_enums():
    expected_types = {"explicit", "preference", "fact", "commitment", "task", "episodic"}
    actual_types = {t.value for t in MemoryType}
    assert actual_types == expected_types

    # Invalid type raises ValidationError
    with pytest.raises(ValidationError):
        MemoryItem(type="invalid_type", content="Test")


def test_memory_source_enums():
    expected_sources = {"user_explicit", "inferred", "system"}
    actual_sources = {s.value for s in MemorySource}
    assert actual_sources == expected_sources


def test_memory_status_enums():
    expected_statuses = {"active", "pending_review", "archived"}
    actual_statuses = {s.value for s in MemoryStatus}
    assert actual_statuses == expected_statuses


def test_validation_bounds():
    # Empty content fails
    with pytest.raises(ValidationError):
        MemoryItem(type=MemoryType.FACT, content="")

    # Importance out of 1-5 range fails
    with pytest.raises(ValidationError):
        MemoryItem(type=MemoryType.FACT, content="Valid", importance=0)
    with pytest.raises(ValidationError):
        MemoryItem(type=MemoryType.FACT, content="Valid", importance=6)

    # Confidence out of 0.0-1.0 range fails
    with pytest.raises(ValidationError):
        MemoryItem(type=MemoryType.FACT, content="Valid", confidence=-0.1)
    with pytest.raises(ValidationError):
        MemoryItem(type=MemoryType.FACT, content="Valid", confidence=1.1)


def test_timezone_aware_utc_coercion():
    # Naive datetime should be coerced to UTC
    naive_now = datetime(2026, 9, 21, 10, 0, 0)
    item = MemoryItem(
        type=MemoryType.PREFERENCE,
        content="Prefers dark theme.",
        created_at=naive_now
    )
    assert item.created_at.tzinfo == timezone.utc

    # Timezone with offset should be converted to UTC
    est_offset = timezone(timedelta(hours=-5))
    est_now = datetime(2026, 9, 21, 10, 0, 0, tzinfo=est_offset)
    item2 = MemoryItem(
        type=MemoryType.PREFERENCE,
        content="Prefers light theme.",
        created_at=est_now
    )
    assert item2.created_at.tzinfo == timezone.utc
    assert item2.created_at.hour == 15  # 10:00 EST -> 15:00 UTC


def test_expiration_behavior():
    now = datetime.now(timezone.utc)

    # Non-expiring
    item_never = MemoryItem(type=MemoryType.FACT, content="Permanent truth")
    assert item_never.is_expired is False

    # Future expiration
    item_future = MemoryItem(
        type=MemoryType.TASK,
        content="Temporary task",
        expires_at=now + timedelta(days=2)
    )
    assert item_future.is_expired is False

    # Past expiration
    item_past = MemoryItem(
        type=MemoryType.TASK,
        content="Expired note",
        expires_at=now - timedelta(hours=1)
    )
    assert item_past.is_expired is True


def test_mark_archived_lifecycle():
    item = MemoryItem(type=MemoryType.PREFERENCE, content="Likes tea.")
    assert item.status == MemoryStatus.ACTIVE
    assert item.is_active is True

    item.mark_archived()
    assert item.status == MemoryStatus.ARCHIVED
    assert item.is_active is False
