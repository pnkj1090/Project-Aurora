import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.services.memory import (
    MemoryType,
    MemorySource,
    MemoryStatus,
    InMemoryMemoryStore,
    MemoryManager,
    get_memory_manager_dependency,
)


@pytest.fixture
def memory_manager():
    store = InMemoryMemoryStore()
    mgr = MemoryManager(store=store)
    orig_create = mgr.create_memory

    async def _create_with_default_user(*args, **kwargs):
        if "user_id" not in kwargs or kwargs["user_id"] is None:
            kwargs["user_id"] = "usr_local_default"
        return await orig_create(*args, **kwargs)

    mgr.create_memory = _create_with_default_user
    return mgr


@pytest.fixture
def setup_api(memory_manager):
    app.dependency_overrides[get_memory_manager_dependency] = lambda: memory_manager
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_explicit_memory_enforces_source_and_confidence(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "content": "I prefer dark mode in all applications.",
            "type": "preference",
            "importance": 4,
            "tags": ["ui", "theme"]
        }
        resp = await client.post("/api/v1/memories", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        assert data["content"] == "I prefer dark mode in all applications."
        assert data["type"] == "preference"
        assert data["importance"] == 4
        assert data["tags"] == ["theme", "ui"]
        assert data["source"] == "user_explicit"
        assert data["status"] == "active"
        assert data["confidence"] == 1.0
        assert data["is_active"] is True
        assert "id" in data
        assert "created_at" in data

        # Verify persisted in store
        saved = await memory_manager.get_memory(data["id"])
        assert saved is not None
        assert saved.source == MemorySource.USER_EXPLICIT
        assert saved.confidence == 1.0
        assert saved.status == MemoryStatus.ACTIVE


@pytest.mark.asyncio
async def test_create_memory_validation_error(setup_api):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Content too short
        resp = await client.post("/api/v1/memories", json={"content": ""})
        assert resp.status_code == 422

        # Importance out of bounds
        resp2 = await client.post("/api/v1/memories", json={"content": "Valid", "importance": 10})
        assert resp2.status_code == 422


@pytest.mark.asyncio
async def test_list_memories_default_active_only(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Active unexpired memory
        await memory_manager.create_memory(content="Active note 1", type=MemoryType.EXPLICIT)
        # Another active memory
        await memory_manager.create_memory(content="Active note 2", type=MemoryType.FACT)

        # Archived memory
        archived, _ = await memory_manager.create_memory(content="Archived note", type=MemoryType.EXPLICIT)
        await memory_manager.delete_memory(archived.id, soft=True)

        # Expired memory
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        await memory_manager.create_memory(content="Expired note", type=MemoryType.TASK, expires_at=past)

        resp = await client.get("/api/v1/memories")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 2
        assert len(data["items"]) == 2
        contents = [m["content"] for m in data["items"]]
        assert "Active note 1" in contents
        assert "Active note 2" in contents
        assert "Archived note" not in contents
        assert "Expired note" not in contents


@pytest.mark.asyncio
async def test_list_memories_pagination(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(15):
            await memory_manager.create_memory(content=f"Item {i:02d}", type=MemoryType.FACT)

        resp1 = await client.get("/api/v1/memories?limit=5&offset=0")
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["total"] == 15
        assert len(d1["items"]) == 5
        assert d1["limit"] == 5
        assert d1["offset"] == 0

        resp2 = await client.get("/api/v1/memories?limit=5&offset=5")
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert len(d2["items"]) == 5
        assert d2["offset"] == 5

        # Check distinct pages
        ids1 = {m["id"] for m in d1["items"]}
        ids2 = {m["id"] for m in d2["items"]}
        assert ids1.isdisjoint(ids2)


@pytest.mark.asyncio
async def test_list_memories_type_filtering(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await memory_manager.create_memory(content="Pref item", type=MemoryType.PREFERENCE)
        await memory_manager.create_memory(content="Fact item", type=MemoryType.FACT)
        await memory_manager.create_memory(content="Task item", type=MemoryType.TASK)

        resp = await client.get("/api/v1/memories?type=preference")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["content"] == "Pref item"
        assert data["items"][0]["type"] == "preference"


@pytest.mark.asyncio
async def test_list_memories_search_query(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await memory_manager.create_memory(content="Schedule morning review with Alice", tags=["calendar"])
        await memory_manager.create_memory(content="Buy groceries after work", tags=["errand"])

        resp = await client.get("/api/v1/memories?q=morning")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert "Alice" in data["items"][0]["content"]

        resp_tag = await client.get("/api/v1/memories?q=calendar")
        assert resp_tag.status_code == 200
        data_tag = resp_tag.json()
        assert data_tag["total"] == 1
        assert "Alice" in data_tag["items"][0]["content"]


@pytest.mark.asyncio
async def test_get_memory_by_id_success(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mem, _ = await memory_manager.create_memory(
            content="Important fact about user",
            type=MemoryType.FACT,
            importance=5,
            tags=["critical"]
        )

        resp = await client.get(f"/api/v1/memories/{mem.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == mem.id
        assert data["content"] == "Important fact about user"
        assert data["type"] == "fact"
        assert data["importance"] == 5
        assert data["source"] == "user_explicit"
        assert data["status"] == "active"
        assert data["is_active"] is True


@pytest.mark.asyncio
async def test_get_memory_404_on_missing_or_archived_or_expired(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Nonexistent
        resp1 = await client.get("/api/v1/memories/mem_nonexistent")
        assert resp1.status_code == 404

        # Archived
        archived, _ = await memory_manager.create_memory(content="Will archive")
        await memory_manager.delete_memory(archived.id, soft=True)
        resp2 = await client.get(f"/api/v1/memories/{archived.id}")
        assert resp2.status_code == 404

        # Expired
        past = datetime.now(timezone.utc) - timedelta(minutes=10)
        expired, _ = await memory_manager.create_memory(content="Expired soon", expires_at=past)
        resp3 = await client.get(f"/api/v1/memories/{expired.id}")
        assert resp3.status_code == 404


@pytest.mark.asyncio
async def test_patch_memory_allowed_fields(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mem, _ = await memory_manager.create_memory(
            content="Initial content",
            importance=2,
            tags=["old"]
        )

        patch_payload = {
            "content": "Updated content with more details",
            "importance": 5,
            "tags": ["updated", "work"]
        }
        resp = await client.patch(f"/api/v1/memories/{mem.id}", json=patch_payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["content"] == "Updated content with more details"
        assert data["importance"] == 5
        assert data["tags"] == ["updated", "work"]

        # Verify persisted
        refreshed = await memory_manager.get_memory(mem.id)
        assert refreshed.content == "Updated content with more details"
        assert refreshed.importance == 5
        assert refreshed.tags == ["updated", "work"]


@pytest.mark.asyncio
async def test_patch_memory_disallowed_fields_cannot_be_modified(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mem, _ = await memory_manager.create_memory(
            content="Immutable fields test",
            importance=3
        )

        # Attempt to pass source or status in PATCH payload
        resp = await client.patch(f"/api/v1/memories/{mem.id}", json={
            "content": "New content",
            "source": "system",
            "status": "archived",
            "confidence": 0.2
        })
        assert resp.status_code == 200
        data = resp.json()

        # Source, status, and confidence must remain intact
        assert data["source"] == "user_explicit"
        assert data["status"] == "active"
        assert data["confidence"] == 1.0


@pytest.mark.asyncio
async def test_patch_memory_not_found(setup_api):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch("/api/v1/memories/mem_missing", json={"content": "New text"})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_memory_soft_only(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mem, _ = await memory_manager.create_memory(content="To be soft-deleted")

        resp = await client.delete(f"/api/v1/memories/{mem.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == mem.id
        assert data["status"] == "archived"
        assert data["deleted"] is True

        # Verify soft-deleted in store
        item = await memory_manager.store.get(mem.id)
        assert item is not None
        assert item.is_active is False
        assert item.status == MemoryStatus.ARCHIVED

        # Verify not returned by normal get
        resp_get = await client.get(f"/api/v1/memories/{mem.id}")
        assert resp_get.status_code == 404


@pytest.mark.asyncio
async def test_delete_memory_404_on_missing_or_already_archived(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Nonexistent
        resp1 = await client.delete("/api/v1/memories/mem_none")
        assert resp1.status_code == 404

        # Already archived
        mem, _ = await memory_manager.create_memory(content="Double delete")
        await client.delete(f"/api/v1/memories/{mem.id}")
        resp2 = await client.delete(f"/api/v1/memories/{mem.id}")
        assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_privacy_and_data_isolation(setup_api, memory_manager):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mem, _ = await memory_manager.create_memory(content="Confidential memory")

        list_resp = await client.get("/api/v1/memories")
        get_resp = await client.get(f"/api/v1/memories/{mem.id}")

        for text in [list_resp.text, get_resp.text]:
            assert "sqlite" not in text.lower()
            assert "aurora.db" not in text.lower()
            assert "table" not in text.lower()
            assert "SELECT" not in text
