import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Callable, List, Optional, Dict, Any, Tuple
from contextlib import asynccontextmanager
import aiosqlite

from app.services.commitments.base import BaseCommitmentStore
from app.services.commitments.models import Commitment, CommitmentStatus, CommitmentPriority


class InMemoryCommitmentStore(BaseCommitmentStore):
    """
    In-memory implementation of BaseCommitmentStore for fast, isolated tests.
    Protected with asyncio.Lock for serialized concurrency.
    """

    def __init__(self):
        # Key: (user_id, commitment_id)
        self._commitments: Dict[tuple[str, str], Commitment] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with self._lock:
            self._commitments.clear()

    async def save_mutation(self, commitment: Commitment) -> tuple[Commitment, bool]:
        """
        Authoritative store-level mutation boundary.
        Under asyncio.Lock:
        1. Compares candidate against persisted state.
        2. If identical (no-op): returns (persisted, False) without advancing updated_at.
        3. If changed: enforces monotonic updated_at (T2 > T1), updates dict, returns (updated, True).
        """
        async with self._lock:
            key = (commitment.user_id, commitment.id)
            persisted = self._commitments.get(key)
            now_utc = datetime.now(timezone.utc)

            if not persisted:
                commitment.created_at = now_utc
                commitment.updated_at = now_utc
                stored = commitment.model_copy(deep=True)
                self._commitments[key] = stored
                return stored.model_copy(deep=True), True

            is_noop = (
                persisted.title == commitment.title and
                (persisted.description or "") == (commitment.description or "") and
                persisted.status == commitment.status and
                persisted.priority == commitment.priority and
                persisted.target_date == commitment.target_date and
                sorted(persisted.tags or []) == sorted(commitment.tags or []) and
                persisted.is_active == commitment.is_active
            )
            if is_noop:
                return persisted.model_copy(deep=True), False

            if now_utc <= persisted.updated_at:
                now_utc = persisted.updated_at + timedelta(microseconds=1)

            commitment.updated_at = now_utc
            commitment.created_at = persisted.created_at
            stored = commitment.model_copy(deep=True)
            self._commitments[key] = stored
            return stored.model_copy(deep=True), True

    async def put(self, commitment: Commitment) -> Commitment:
        res, _ = await self.save_mutation(commitment)
        return res

    async def mutate(self, commitment_id: str, user_id: str, mutation: Callable[[Commitment], None]) -> Tuple[Optional[Commitment], bool]:
        async with self._lock:
            key = (user_id, commitment_id)
            persisted = self._commitments.get(key)
            if persisted is None:
                return None, False
            candidate = persisted.model_copy(deep=True)
            mutation(candidate)
            candidate_state = candidate.model_dump(exclude={"created_at", "updated_at"})
            persisted_state = persisted.model_dump(exclude={"created_at", "updated_at"})
            candidate_state["tags"] = sorted(candidate.tags)
            persisted_state["tags"] = sorted(persisted.tags)
            if candidate_state == persisted_state:
                return persisted.model_copy(deep=True), False
            now_utc = datetime.now(timezone.utc)
            if now_utc <= persisted.updated_at:
                now_utc = persisted.updated_at + timedelta(microseconds=1)
            candidate.created_at = persisted.created_at
            candidate.updated_at = now_utc
            self._commitments[key] = candidate.model_copy(deep=True)
            return candidate.model_copy(deep=True), True

    async def get(self, commitment_id: str, user_id: str) -> Optional[Commitment]:
        async with self._lock:
            item = self._commitments.get((user_id, commitment_id))
            if item is None:
                return None
            return item.model_copy(deep=True)

    async def delete(self, commitment_id: str, user_id: str, soft: bool = True) -> bool:
        if soft:
            _, changed = await self.mutate(commitment_id, user_id, lambda item: setattr(item, "is_active", False))
            return changed
        async with self._lock:
            key = (user_id, commitment_id)
            item = self._commitments.get(key)
            if item is None:
                return False
            del self._commitments[key]
            return True

    async def list_commitments(
        self,
        user_id: str,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Commitment]:
        async with self._lock:
            matches: List[Commitment] = []
            q_lower = query.strip().lower() if query else None

            for (uid, _), item in self._commitments.items():
                if uid != user_id:
                    continue
                if not include_inactive and not item.is_active:
                    continue
                if statuses and item.status not in statuses:
                    continue
                if priorities and item.priority not in priorities:
                    continue
                if tags and not any(tag in item.tags for tag in tags):
                    continue
                if q_lower:
                    in_title = q_lower in item.title.lower()
                    in_desc = q_lower in (item.description or "").lower()
                    in_tags = any(q_lower in tag.lower() for tag in item.tags)
                    if not (in_title or in_desc or in_tags):
                        continue
                matches.append(item)

            prio_weight = {CommitmentPriority.HIGH: 3, CommitmentPriority.MEDIUM: 2, CommitmentPriority.LOW: 1}
            matches.sort(
                key=lambda x: (
                    prio_weight.get(x.priority, 0),
                    0 if x.target_date else 1,
                    x.target_date or datetime.max.replace(tzinfo=timezone.utc),
                    x.created_at
                ),
                reverse=True
            )

            return [m.model_copy(deep=True) for m in matches[offset:offset + limit]]

    async def count(
        self,
        user_id: str,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        async with self._lock:
            count = 0
            q_lower = query.strip().lower() if query else None

            for (uid, _), item in self._commitments.items():
                if uid != user_id:
                    continue
                if not include_inactive and not item.is_active:
                    continue
                if statuses and item.status not in statuses:
                    continue
                if priorities and item.priority not in priorities:
                    continue
                if tags and not any(tag in item.tags for tag in tags):
                    continue
                if q_lower:
                    in_title = q_lower in item.title.lower()
                    in_desc = q_lower in (item.description or "").lower()
                    in_tags = any(q_lower in tag.lower() for tag in item.tags)
                    if not (in_title or in_desc or in_tags):
                        continue
                count += 1
            return count

    async def close(self) -> None:
        pass


class SQLiteCommitmentStore(BaseCommitmentStore):
    """
    Persistent SQLite implementation of BaseCommitmentStore using aiosqlite.
    Integrates directly with the central `data/aurora.db`.
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
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute("PRAGMA journal_mode = WAL;")
            await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS commitments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    target_date TEXT,
                    completed_at TEXT,
                    tags TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    source_memory_id TEXT
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_commitments_user_id ON commitments(user_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_commitments_priority ON commitments(priority);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_commitments_target_date ON commitments(target_date);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_commitments_is_active ON commitments(is_active);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_commitments_created_at ON commitments(created_at);")
            await conn.commit()

        self._initialized = True

    def _row_to_item(self, row: aiosqlite.Row) -> Commitment:
        return Commitment(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            description=row["description"],
            status=CommitmentStatus(row["status"]),
            priority=CommitmentPriority(row["priority"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            target_date=datetime.fromisoformat(row["target_date"]) if row["target_date"] else None,
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            tags=json.loads(row["tags"]) if row["tags"] else [],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            is_active=bool(row["is_active"]),
            source_memory_id=row["source_memory_id"]
        )

    async def save_mutation(self, commitment: Commitment) -> tuple[Commitment, bool]:
        """
        Authoritative store-level mutation boundary.
        Executes serialized SQLite write transaction with BEGIN IMMEDIATE:
        1. Acquires SQLite writer lock BEFORE reading state.
        2. Reads latest persisted state inside the write lock.
        3. Evaluates strict field-by-field no-op. If no-op: rollbacks and returns (persisted, False).
        4. Enforces strict timestamp monotonicity (T2 > T1) if changed.
        5. Updates and commits atomically, returning (updated, True).
        """
        async with self._get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = await conn.execute(
                    "SELECT * FROM commitments WHERE id = ? AND user_id = ?;",
                    (commitment.id, commitment.user_id)
                )
                row = await cursor.fetchone()

                now_utc = datetime.now(timezone.utc)

                if not row:
                    commitment.created_at = now_utc
                    commitment.updated_at = now_utc
                    await conn.execute("""
                        INSERT INTO commitments (
                            id, user_id, title, description, status, priority,
                            created_at, updated_at, target_date, completed_at,
                            tags, metadata, is_active, source_memory_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        commitment.id,
                        commitment.user_id,
                        commitment.title,
                        commitment.description or "",
                        commitment.status.value,
                        commitment.priority.value,
                        commitment.created_at.isoformat(),
                        commitment.updated_at.isoformat(),
                        commitment.target_date.isoformat() if commitment.target_date else None,
                        commitment.completed_at.isoformat() if commitment.completed_at else None,
                        json.dumps(commitment.tags),
                        json.dumps(commitment.metadata),
                        1 if commitment.is_active else 0,
                        commitment.source_memory_id
                    ))
                    await conn.commit()
                    return commitment.model_copy(deep=True), True

                persisted = self._row_to_item(row)

                # Strict field-by-field no-op comparison
                is_noop = (
                    persisted.title == commitment.title and
                    (persisted.description or "") == (commitment.description or "") and
                    persisted.status == commitment.status and
                    persisted.priority == commitment.priority and
                    persisted.target_date == commitment.target_date and
                    sorted(persisted.tags or []) == sorted(commitment.tags or []) and
                    persisted.is_active == commitment.is_active
                )
                if is_noop:
                    await conn.rollback()
                    return persisted, False

                # Monotonic updated_at progression: T2 > T1
                if now_utc <= persisted.updated_at:
                    now_utc = persisted.updated_at + timedelta(microseconds=1)

                commitment.updated_at = now_utc
                commitment.created_at = persisted.created_at

                await conn.execute("""
                    UPDATE commitments SET
                        title = ?, description = ?, status = ?, priority = ?,
                        updated_at = ?, target_date = ?, completed_at = ?,
                        tags = ?, metadata = ?, is_active = ?, source_memory_id = ?
                    WHERE id = ? AND user_id = ?
                """, (
                    commitment.title,
                    commitment.description or "",
                    commitment.status.value,
                    commitment.priority.value,
                    commitment.updated_at.isoformat(),
                    commitment.target_date.isoformat() if commitment.target_date else None,
                    commitment.completed_at.isoformat() if commitment.completed_at else None,
                    json.dumps(commitment.tags),
                    json.dumps(commitment.metadata),
                    1 if commitment.is_active else 0,
                    commitment.source_memory_id,
                    commitment.id,
                    commitment.user_id
                ))
                await conn.commit()
                return commitment.model_copy(deep=True), True
            except Exception:
                await conn.rollback()
                raise

    async def mutate(self, commitment_id: str, user_id: str, mutation: Callable[[Commitment], None]) -> Tuple[Optional[Commitment], bool]:
        async with self._get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = await conn.execute("SELECT * FROM commitments WHERE id = ? AND user_id = ?;", (commitment_id, user_id))
                row = await cursor.fetchone()
                if row is None:
                    await conn.rollback()
                    return None, False
                persisted = self._row_to_item(row)
                candidate = persisted.model_copy(deep=True)
                mutation(candidate)
                candidate_state = candidate.model_dump(exclude={"created_at", "updated_at"})
                persisted_state = persisted.model_dump(exclude={"created_at", "updated_at"})
                candidate_state["tags"] = sorted(candidate.tags)
                persisted_state["tags"] = sorted(persisted.tags)
                if candidate_state == persisted_state:
                    await conn.rollback()
                    return persisted, False
                now_utc = datetime.now(timezone.utc)
                if now_utc <= persisted.updated_at:
                    now_utc = persisted.updated_at + timedelta(microseconds=1)
                candidate.created_at = persisted.created_at
                candidate.updated_at = now_utc
                await conn.execute(
                    """UPDATE commitments SET title=?, description=?, status=?, priority=?, updated_at=?, target_date=?, completed_at=?,
                       tags=?, metadata=?, is_active=?, source_memory_id=? WHERE id=? AND user_id=?""",
                    (candidate.title, candidate.description or "", candidate.status.value, candidate.priority.value,
                     candidate.updated_at.isoformat(), candidate.target_date.isoformat() if candidate.target_date else None,
                     candidate.completed_at.isoformat() if candidate.completed_at else None, json.dumps(candidate.tags),
                     json.dumps(candidate.metadata), 1 if candidate.is_active else 0, candidate.source_memory_id,
                     commitment_id, user_id),
                )
                await conn.commit()
                return candidate, True
            except Exception:
                await conn.rollback()
                raise

    async def put(self, commitment: Commitment) -> Commitment:
        res, _ = await self.save_mutation(commitment)
        return res

    async def get(self, commitment_id: str, user_id: str) -> Optional[Commitment]:
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM commitments WHERE id = ? AND user_id = ?;",
                (commitment_id, user_id)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    async def delete(self, commitment_id: str, user_id: str, soft: bool = True) -> bool:
        if soft:
            _, changed = await self.mutate(commitment_id, user_id, lambda item: setattr(item, "is_active", False))
            return changed
        async with self._get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            cursor = await conn.execute("DELETE FROM commitments WHERE id = ? AND user_id = ?;", (commitment_id, user_id))
            await conn.commit()
            return cursor.rowcount > 0

    async def list_commitments(
        self,
        user_id: str,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Commitment]:
        where_clauses = ["user_id = ?"]
        params: List[Any] = [user_id]

        if not include_inactive:
            where_clauses.append("is_active = 1")

        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where_clauses.append(f"status IN ({placeholders})")
            params.extend(s.value for s in statuses)

        if priorities:
            placeholders = ",".join("?" for _ in priorities)
            where_clauses.append(f"priority IN ({placeholders})")
            params.extend(p.value for p in priorities)

        if query:
            where_clauses.append("(title LIKE ? OR description LIKE ? OR tags LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern, pattern])

        where_sql = " AND ".join(where_clauses)
        query_sql = f"""
            SELECT * FROM commitments
            WHERE {where_sql}
            ORDER BY
                CASE priority WHEN 'high' THEN 3 WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC,
                CASE WHEN target_date IS NULL THEN 1 ELSE 0 END ASC,
                target_date ASC,
                created_at DESC
            LIMIT ? OFFSET ?;
        """
        params.extend([limit, offset])

        async with self._get_connection() as conn:
            cursor = await conn.execute(query_sql, params)
            rows = await cursor.fetchall()
            items = [self._row_to_item(r) for r in rows]

            if tags:
                items = [c for c in items if any(tag in c.tags for tag in tags)]

            return items

    async def count(
        self,
        user_id: str,
        statuses: Optional[List[CommitmentStatus]] = None,
        priorities: Optional[List[CommitmentPriority]] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        where_clauses = ["user_id = ?"]
        params: List[Any] = [user_id]

        if not include_inactive:
            where_clauses.append("is_active = 1")

        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where_clauses.append(f"status IN ({placeholders})")
            params.extend(s.value for s in statuses)

        if priorities:
            placeholders = ",".join("?" for _ in priorities)
            where_clauses.append(f"priority IN ({placeholders})")
            params.extend(p.value for p in priorities)

        if query:
            where_clauses.append("(title LIKE ? OR description LIKE ? OR tags LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern, pattern])

        where_sql = " AND ".join(where_clauses)
        count_sql = f"SELECT COUNT(*) as total FROM commitments WHERE {where_sql};"

        async with self._get_connection() as conn:
            cursor = await conn.execute(count_sql, params)
            row = await cursor.fetchone()
            return int(row["total"]) if row else 0

    async def close(self) -> None:
        pass
