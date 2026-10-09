import os
import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
import aiosqlite

from app.services.memory.base import BaseMemoryStore
from app.services.memory.schemas import MemoryItem, MemoryType, MemoryStatus, MemorySource


class InMemoryMemoryStore(BaseMemoryStore):
    """
    In-memory implementation of BaseMemoryStore.
    Provides complete functional parity with SQLiteMemoryStore for fast, isolated tests.
    """

    def __init__(self):
        self._memories: Dict[str, MemoryItem] = {}

    async def initialize(self) -> None:
        self._memories.clear()

    async def put(self, memory: MemoryItem) -> MemoryItem:
        # Clone memory to prevent external mutation and update updated_at
        now = datetime.now(timezone.utc)
        stored_item = memory.model_copy(deep=True)
        stored_item.updated_at = now
        self._memories[stored_item.id] = stored_item
        return stored_item.model_copy(deep=True)

    async def get(self, memory_id: str) -> Optional[MemoryItem]:
        item = self._memories.get(memory_id)
        if item is None:
            return None
        return item.model_copy(deep=True)

    async def delete(self, memory_id: str, soft: bool = True) -> bool:
        item = self._memories.get(memory_id)
        if item is None:
            return False

        if soft:
            item.mark_archived()
            return True
        else:
            del self._memories[memory_id]
            return True

    async def list_memories(
        self,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryItem]:
        results: List[MemoryItem] = []
        q_lower = query.strip().lower() if query else None

        for item in self._memories.values():
            if not include_inactive and not item.is_active:
                continue
            if not include_expired and item.is_expired:
                continue
            if types and item.type not in types:
                continue
            if statuses and item.status not in statuses:
                continue
            if source and item.source != source:
                continue
            # User isolation filtering
            if user_id is not None:
                item_uid = item.user_id
                if item_uid != user_id:
                    if not (allow_legacy_unscoped and item_uid is None):
                        continue
            elif not allow_legacy_unscoped:
                continue

            if tags and not any(tag in item.tags for tag in tags):
                continue
            if q_lower:
                in_content = q_lower in item.content.lower()
                in_tags = any(q_lower in tag.lower() for tag in item.tags)
                in_type = q_lower in item.type.value.lower()
                if not (in_content or in_tags or in_type):
                    continue
            results.append(item.model_copy(deep=True))

        # Sort by created_at descending
        results.sort(key=lambda m: m.created_at, reverse=True)
        return results[offset : offset + limit]

    async def search_lexical(
        self,
        query: str,
        types: Optional[List[MemoryType]] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        limit: int = 10,
    ) -> List[MemoryItem]:
        clean_query = query.strip().lower()
        if not clean_query:
            return []

        tokens = [t for t in clean_query.split() if len(t) > 1]
        scored_items: List[tuple] = []

        for item in self._memories.values():
            if not include_inactive and not item.is_active:
                continue
            if not include_expired and item.is_expired:
                continue
            if types and item.type not in types:
                continue

            content_lower = item.content.lower()
            tags_lower = [t.lower() for t in item.tags]
            type_lower = item.type.value.lower()

            score = 0
            # Exact phrase match in content
            if clean_query in content_lower:
                score += 10

            # Token overlap in content, tags, or type
            for token in tokens:
                if token in content_lower:
                    score += 3
                if any(token in tag for tag in tags_lower):
                    score += 4
                if token in type_lower:
                    score += 2

            if score > 0:
                final_score = score * (item.importance / 3.0)
                scored_items.append((final_score, item.created_at, item))

        # Sort by score desc, then created_at desc
        scored_items.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item.model_copy(deep=True) for _, _, item in scored_items[:limit]]

    async def count(
        self,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = True,
    ) -> int:
        count = 0
        q_lower = query.strip().lower() if query else None

        for item in self._memories.values():
            if not include_inactive and not item.is_active:
                continue
            if not include_expired and item.is_expired:
                continue
            if types and item.type not in types:
                continue
            if statuses and item.status not in statuses:
                continue
            if source and item.source != source:
                continue
            # User isolation filtering
            if user_id is not None:
                item_uid = item.user_id
                if item_uid != user_id:
                    if not (allow_legacy_unscoped and item_uid is None):
                        continue
            elif not allow_legacy_unscoped:
                continue

            if tags and not any(tag in item.tags for tag in tags):
                continue
            if q_lower:
                in_content = q_lower in item.content.lower()
                in_tags = any(q_lower in tag.lower() for tag in item.tags)
                in_type = q_lower in item.type.value.lower()
                if not (in_content or in_tags or in_type):
                    continue
            count += 1
        return count

    def search_candidates_sync(
        self,
        query: Optional[str] = None,
        memory_id: Optional[str] = None,
        limit: int = 5
    ) -> List[MemoryItem]:
        results: List[MemoryItem] = []
        if memory_id:
            item = self._memories.get(memory_id)
            if item and item.is_active and not item.is_expired:
                return [item.model_copy(deep=True)]
            return []

        if not query:
            return []

        clean_query = query.strip().lower()
        tokens = [t for t in clean_query.split() if len(t) > 1]
        for item in self._memories.values():
            if not item.is_active or item.is_expired:
                continue
            content_lower = item.content.lower()
            tags_lower = [t.lower() for t in item.tags]
            if clean_query in content_lower or any(t in content_lower for t in tokens) or any(t in tags_lower for t in tokens):
                results.append(item.model_copy(deep=True))
                if len(results) >= limit:
                    break
        return results

    async def close(self) -> None:
        pass


class SQLiteMemoryStore(BaseMemoryStore):
    """
    Persistent SQLite implementation of BaseMemoryStore using aiosqlite.
    Zero-external dependency, local-first, ACID-compliant personal memory store.
    """

    def __init__(self, db_path: str = "data/aurora.db"):
        self.db_path = db_path
        self._initialized = False

    @asynccontextmanager
    async def _get_connection(self):
        if not self._initialized:
            await self.initialize()
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def initialize(self) -> None:
        """Create database directory and table schemas if not existing."""
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 3,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    source TEXT NOT NULL DEFAULT 'user_explicit',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    tags TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    is_active INTEGER NOT NULL DEFAULT 1
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_is_active ON memories(is_active);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);")
            await conn.commit()

        self._initialized = True

    def _row_to_item(self, row: aiosqlite.Row) -> MemoryItem:
        """Helper to deserialize an SQLite Row into a typed MemoryItem."""
        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = datetime.fromisoformat(row["updated_at"])
        expires_at = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None

        tags = json.loads(row["tags"]) if row["tags"] else []
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}

        return MemoryItem(
            id=row["id"],
            type=MemoryType(row["type"]),
            content=row["content"],
            importance=row["importance"],
            confidence=row["confidence"],
            source=MemorySource(row["source"]),
            status=MemoryStatus(row["status"]),
            created_at=created_at,
            updated_at=updated_at,
            expires_at=expires_at,
            tags=tags,
            metadata=metadata,
            is_active=bool(row["is_active"])
        )

    async def put(self, memory: MemoryItem) -> MemoryItem:
        now = datetime.now(timezone.utc)
        stored_item = memory.model_copy(deep=True)
        stored_item.updated_at = now

        query = """
            INSERT INTO memories (
                id, type, content, importance, confidence, source, status,
                created_at, updated_at, expires_at, tags, metadata, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type = excluded.type,
                content = excluded.content,
                importance = excluded.importance,
                confidence = excluded.confidence,
                source = excluded.source,
                status = excluded.status,
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at,
                tags = excluded.tags,
                metadata = excluded.metadata,
                is_active = excluded.is_active;
        """

        params = (
            stored_item.id,
            stored_item.type.value,
            stored_item.content,
            stored_item.importance,
            stored_item.confidence,
            stored_item.source.value,
            stored_item.status.value,
            stored_item.created_at.isoformat(),
            stored_item.updated_at.isoformat(),
            stored_item.expires_at.isoformat() if stored_item.expires_at else None,
            json.dumps(stored_item.tags),
            json.dumps(stored_item.metadata),
            1 if stored_item.is_active else 0,
        )

        async with self._get_connection() as conn:
            await conn.execute(query, params)
            await conn.commit()

        return stored_item

    async def get(self, memory_id: str) -> Optional[MemoryItem]:
        async with self._get_connection() as conn:
            async with conn.execute("SELECT * FROM memories WHERE id = ?;", (memory_id,)) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return self._row_to_item(row)

    async def delete(self, memory_id: str, soft: bool = True) -> bool:
        async with self._get_connection() as conn:
            if soft:
                now_iso = datetime.now(timezone.utc).isoformat()
                cursor = await conn.execute(
                    "UPDATE memories SET is_active = 0, status = ?, updated_at = ? WHERE id = ? AND is_active = 1;",
                    (MemoryStatus.ARCHIVED.value, now_iso, memory_id)
                )
            else:
                cursor = await conn.execute("DELETE FROM memories WHERE id = ?;", (memory_id,))

            await conn.commit()
            return cursor.rowcount > 0

    async def list_memories(
        self,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryItem]:
        conditions = []
        params = []

        if not include_inactive:
            conditions.append("is_active = 1")

        if not include_expired:
            now_iso = datetime.now(timezone.utc).isoformat()
            conditions.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(now_iso)

        if user_id is not None:
            if allow_legacy_unscoped:
                conditions.append("(json_extract(metadata, '$.user_id') = ? OR json_extract(metadata, '$.user_id') IS NULL)")
                params.append(user_id)
            else:
                conditions.append("json_extract(metadata, '$.user_id') = ?")
                params.append(user_id)
        elif not allow_legacy_unscoped:
            conditions.append("1 = 0")

        if types:
            placeholders = ",".join(["?"] * len(types))
            conditions.append(f"type IN ({placeholders})")
            params.extend([t.value for t in types])

        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend([s.value for s in statuses])

        if source:
            conditions.append("source = ?")
            params.append(source.value)

        if query:
            clean_q = query.strip().lower()
            if clean_q:
                conditions.append("(LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)")
                params.extend([f"%{clean_q}%", f"%{clean_q}%"])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql_query = f"SELECT * FROM memories {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        results: List[MemoryItem] = []
        async with self._get_connection() as conn:
            async with conn.execute(sql_query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    item = self._row_to_item(row)
                    # Filter tags in Python if tag criteria specified
                    if tags and not any(t in item.tags for t in tags):
                        continue
                    results.append(item)

        return results

    async def search_lexical(
        self,
        query: str,
        types: Optional[List[MemoryType]] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        limit: int = 10,
    ) -> List[MemoryItem]:
        clean_query = query.strip().lower()
        if not clean_query:
            return []

        conditions = []
        params = []

        if not include_inactive:
            conditions.append("is_active = 1")

        if not include_expired:
            now_iso = datetime.now(timezone.utc).isoformat()
            conditions.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(now_iso)

        if types:
            placeholders = ",".join(["?"] * len(types))
            conditions.append(f"type IN ({placeholders})")
            params.extend([t.value for t in types])

        tokens = [t for t in clean_query.split() if len(t) > 1]
        search_clauses = []
        # Match full query or any token
        search_clauses.append("LOWER(content) LIKE ?")
        params.append(f"%{clean_query}%")
        search_clauses.append("LOWER(tags) LIKE ?")
        params.append(f"%{clean_query}%")

        for token in tokens[:5]:
            search_clauses.append("LOWER(content) LIKE ?")
            params.append(f"%{token}%")
            search_clauses.append("LOWER(tags) LIKE ?")
            params.append(f"%{token}%")

        conditions.append(f"({' OR '.join(search_clauses)})")
        where_clause = f"WHERE {' AND '.join(conditions)}"

        # Custom ranking score: exact phrase match gets 4x, multiplied by importance
        exact_like = f"%{clean_query}%"
        sql = f"""
            SELECT *,
                ((CASE WHEN LOWER(content) LIKE ? THEN 4 ELSE 1 END) * importance) AS rank_score
            FROM memories
            {where_clause}
            ORDER BY rank_score DESC, created_at DESC
            LIMIT ?;
        """
        full_params = [exact_like] + params + [limit]

        async with self._get_connection() as conn:
            async with conn.execute(sql, tuple(full_params)) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_item(row) for row in rows]

    async def count(
        self,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = True,
    ) -> int:
        conditions = []
        params = []
        if not include_inactive:
            conditions.append("is_active = 1")
        if not include_expired:
            now_iso = datetime.now(timezone.utc).isoformat()
            conditions.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(now_iso)

        if user_id is not None:
            if allow_legacy_unscoped:
                conditions.append("(json_extract(metadata, '$.user_id') = ? OR json_extract(metadata, '$.user_id') IS NULL)")
                params.append(user_id)
            else:
                conditions.append("json_extract(metadata, '$.user_id') = ?")
                params.append(user_id)
        elif not allow_legacy_unscoped:
            conditions.append("1 = 0")

        if types:
            placeholders = ",".join(["?"] * len(types))
            conditions.append(f"type IN ({placeholders})")
            params.extend([t.value for t in types])

        if statuses:
            placeholders = ",".join(["?"] * len(statuses))
            conditions.append(f"status IN ({placeholders})")
            params.extend([s.value for s in statuses])

        if source:
            conditions.append("source = ?")
            params.append(source.value)

        if query:
            clean_q = query.strip().lower()
            if clean_q:
                conditions.append("(LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)")
                params.extend([f"%{clean_q}%", f"%{clean_q}%"])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT COUNT(*) FROM memories {where_clause};"

        async with self._get_connection() as conn:
            async with conn.execute(sql, tuple(params)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    def search_candidates_sync(
        self,
        query: Optional[str] = None,
        memory_id: Optional[str] = None,
        limit: int = 5
    ) -> List[MemoryItem]:
        import sqlite3
        if not os.path.exists(self.db_path):
            return []

        if memory_id:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM memories WHERE id = ? AND is_active = 1;",
                    (memory_id,)
                )
                row = cursor.fetchone()
                if row:
                    item = self._row_to_item(row)
                    if not item.is_expired:
                        return [item]
            return []

        if not query:
            return []

        clean_query = query.strip().lower()
        like_pat = f"%{clean_query}%"
        now_iso = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM memories
                WHERE is_active = 1
                  AND (expires_at IS NULL OR expires_at > ?)
                  AND (LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)
                ORDER BY importance DESC, created_at DESC
                LIMIT ?;
                """,
                (now_iso, like_pat, like_pat, limit)
            )
            rows = cursor.fetchall()
            return [self._row_to_item(row) for row in rows]

    async def close(self) -> None:
        pass
