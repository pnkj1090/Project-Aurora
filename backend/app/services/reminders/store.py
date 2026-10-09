import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple
from contextlib import asynccontextmanager
import aiosqlite

from app.services.reminders.base import BaseReminderStore
from app.services.reminders.models import Reminder, ReminderStatus


class InMemoryReminderStore(BaseReminderStore):
    """
    In-memory implementation of BaseReminderStore for fast, isolated tests.
    """

    def __init__(self):
        # Key: (user_id, reminder_id)
        self._reminders: Dict[tuple[str, str], Reminder] = {}
        self._lock: Optional[asyncio.Lock] = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def initialize(self) -> None:
        self._reminders.clear()

    async def save_mutation(self, reminder: Reminder) -> Tuple[Reminder, bool]:
        async with self.lock:
            key = (reminder.user_id, reminder.id)
            existing = self._reminders.get(key)
            now_utc = datetime.now(timezone.utc)

            if existing is None:
                stored = reminder.model_copy(deep=True)
                if stored.created_at is None:
                    stored.created_at = now_utc
                stored.updated_at = now_utc
                self._reminders[key] = stored
                return stored.model_copy(deep=True), True

            # Strict no-op check
            is_noop = (
                existing.title == reminder.title and
                (existing.body or "") == (reminder.body or "") and
                existing.scheduled_at == reminder.scheduled_at and
                existing.status == reminder.status and
                existing.task_id == reminder.task_id and
                existing.commitment_id == reminder.commitment_id and
                existing.notification_id == reminder.notification_id and
                existing.is_active == reminder.is_active
            )
            if is_noop:
                return existing.model_copy(deep=True), False

            # Strict monotonicity: T2 > T1
            if now_utc <= existing.updated_at:
                now_utc = existing.updated_at + timedelta(microseconds=1)

            stored = reminder.model_copy(deep=True)
            stored.updated_at = now_utc
            stored.created_at = existing.created_at
            self._reminders[key] = stored
            return stored.model_copy(deep=True), True

    async def put(self, reminder: Reminder) -> Reminder:
        res, _ = await self.save_mutation(reminder)
        return res

    async def get(self, reminder_id: str, user_id: str) -> Optional[Reminder]:
        item = self._reminders.get((user_id, reminder_id))
        if item is None:
            return None
        return item.model_copy(deep=True)

    async def delete(self, reminder_id: str, user_id: str, soft: bool = True) -> bool:
        key = (user_id, reminder_id)
        item = self._reminders.get(key)
        if item is None:
            return False

        if soft:
            item.is_active = False
            item.updated_at = datetime.now(timezone.utc)
            return True
        else:
            del self._reminders[key]
            return True

    async def list_reminders(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Reminder]:
        matches: List[Reminder] = []
        q_lower = query.strip().lower() if query else None

        for (uid, _), item in self._reminders.items():
            if uid != user_id:
                continue
            if not include_inactive and not item.is_active:
                continue
            if statuses and item.status not in statuses:
                continue
            if task_id and item.task_id != task_id:
                continue
            if commitment_id and item.commitment_id != commitment_id:
                continue
            if q_lower:
                in_title = q_lower in item.title.lower()
                in_body = q_lower in (item.body or "").lower()
                if not (in_title or in_body):
                    continue
            matches.append(item)

        # Sort: scheduled_at asc, created_at desc
        matches.sort(
            key=lambda x: (x.scheduled_at, -x.created_at.timestamp())
        )

        return [m.model_copy(deep=True) for m in matches[offset:offset + limit]]

    async def count(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        query: Optional[str] = None,
        include_inactive: bool = False
    ) -> int:
        count = 0
        q_lower = query.strip().lower() if query else None

        for (uid, _), item in self._reminders.items():
            if uid != user_id:
                continue
            if not include_inactive and not item.is_active:
                continue
            if statuses and item.status not in statuses:
                continue
            if task_id and item.task_id != task_id:
                continue
            if commitment_id and item.commitment_id != commitment_id:
                continue
            if q_lower:
                in_title = q_lower in item.title.lower()
                in_body = q_lower in (item.body or "").lower()
                if not (in_title or in_body):
                    continue
            count += 1
        return count

    async def close(self) -> None:
        pass


class SQLiteReminderStore(BaseReminderStore):
    """
    Persistent SQLite implementation of BaseReminderStore using aiosqlite.
    Integrates directly with central `data/aurora.db`.
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
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    task_id TEXT,
                    commitment_id TEXT,
                    notification_id TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user_id ON reminders(user_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_scheduled_at ON reminders(scheduled_at);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_is_active ON reminders(is_active);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_task_id ON reminders(task_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_commitment_id ON reminders(commitment_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_created_at ON reminders(created_at);")
            await conn.commit()

        self._initialized = True

    def _row_to_item(self, row: aiosqlite.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            body=row["body"],
            scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
            status=ReminderStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            task_id=row["task_id"],
            commitment_id=row["commitment_id"],
            notification_id=row["notification_id"],
            is_active=bool(row["is_active"])
        )

    async def save_mutation(self, reminder: Reminder) -> Tuple[Reminder, bool]:
        if not self._initialized:
            await self.initialize()

        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            # 1. Acquire SQLite write lock IMMEDIATELY before reading persisted state
            await conn.execute("BEGIN IMMEDIATE")
            try:
                # 2. Read latest persisted state inside the write transaction
                cursor = await conn.execute(
                    "SELECT * FROM reminders WHERE id = ? AND user_id = ?;",
                    (reminder.id, reminder.user_id)
                )
                row = await cursor.fetchone()
                now_utc = datetime.now(timezone.utc)

                if row is None:
                    # New record insertion
                    if reminder.created_at is None:
                        reminder.created_at = now_utc
                    reminder.updated_at = now_utc

                    await conn.execute("""
                        INSERT INTO reminders (
                            id, user_id, title, body, scheduled_at, status,
                            created_at, updated_at, task_id, commitment_id,
                            notification_id, is_active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        reminder.id,
                        reminder.user_id,
                        reminder.title,
                        reminder.body or "",
                        reminder.scheduled_at.isoformat(),
                        reminder.status.value,
                        reminder.created_at.isoformat(),
                        reminder.updated_at.isoformat(),
                        reminder.task_id,
                        reminder.commitment_id,
                        reminder.notification_id,
                        1 if reminder.is_active else 0
                    ))
                    await conn.commit()
                    return reminder.model_copy(deep=True), True

                persisted = self._row_to_item(row)

                # 3. Strict no-op check
                is_noop = (
                    persisted.title == reminder.title and
                    (persisted.body or "") == (reminder.body or "") and
                    persisted.scheduled_at == reminder.scheduled_at and
                    persisted.status == reminder.status and
                    persisted.task_id == reminder.task_id and
                    persisted.commitment_id == reminder.commitment_id and
                    persisted.notification_id == reminder.notification_id and
                    persisted.is_active == reminder.is_active
                )
                if is_noop:
                    await conn.rollback()
                    return persisted, False

                # 4. Strict monotonicity: T2 > T1
                if now_utc <= persisted.updated_at:
                    now_utc = persisted.updated_at + timedelta(microseconds=1)

                reminder.updated_at = now_utc
                reminder.created_at = persisted.created_at

                # 5. Persist mutation atomically
                await conn.execute("""
                    UPDATE reminders SET
                        title = ?, body = ?, scheduled_at = ?, status = ?,
                        updated_at = ?, task_id = ?, commitment_id = ?,
                        notification_id = ?, is_active = ?
                    WHERE id = ? AND user_id = ?
                """, (
                    reminder.title,
                    reminder.body or "",
                    reminder.scheduled_at.isoformat(),
                    reminder.status.value,
                    reminder.updated_at.isoformat(),
                    reminder.task_id,
                    reminder.commitment_id,
                    reminder.notification_id,
                    1 if reminder.is_active else 0,
                    reminder.id,
                    reminder.user_id
                ))
                await conn.commit()
                return reminder.model_copy(deep=True), True
            except Exception:
                await conn.rollback()
                raise

    async def put(self, reminder: Reminder) -> Reminder:
        res, _ = await self.save_mutation(reminder)
        return res

    async def get(self, reminder_id: str, user_id: str) -> Optional[Reminder]:
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM reminders WHERE id = ? AND user_id = ?;",
                (reminder_id, user_id)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    async def delete(self, reminder_id: str, user_id: str, soft: bool = True) -> bool:
        async with self._get_connection() as conn:
            if soft:
                cursor = await conn.execute(
                    "UPDATE reminders SET is_active = 0, updated_at = ? WHERE id = ? AND user_id = ? AND is_active = 1;",
                    (datetime.now(timezone.utc).isoformat(), reminder_id, user_id)
                )
            else:
                cursor = await conn.execute(
                    "DELETE FROM reminders WHERE id = ? AND user_id = ?;",
                    (reminder_id, user_id)
                )
            await conn.commit()
            return cursor.rowcount > 0

    async def list_reminders(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> List[Reminder]:
        where_clauses = ["user_id = ?"]
        params: List[Any] = [user_id]

        if not include_inactive:
            where_clauses.append("is_active = 1")

        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where_clauses.append(f"status IN ({placeholders})")
            params.extend(s.value for s in statuses)

        if task_id:
            where_clauses.append("task_id = ?")
            params.append(task_id)

        if commitment_id:
            where_clauses.append("commitment_id = ?")
            params.append(commitment_id)

        if query:
            where_clauses.append("(title LIKE ? OR body LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern])

        where_sql = " AND ".join(where_clauses)
        query_sql = f"""
            SELECT * FROM reminders
            WHERE {where_sql}
            ORDER BY scheduled_at ASC, created_at DESC
            LIMIT ? OFFSET ?;
        """
        params.extend([limit, offset])

        async with self._get_connection() as conn:
            cursor = await conn.execute(query_sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_item(r) for r in rows]

    async def count(
        self,
        user_id: str,
        statuses: Optional[List[ReminderStatus]] = None,
        task_id: Optional[str] = None,
        commitment_id: Optional[str] = None,
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

        if task_id:
            where_clauses.append("task_id = ?")
            params.append(task_id)

        if commitment_id:
            where_clauses.append("commitment_id = ?")
            params.append(commitment_id)

        if query:
            where_clauses.append("(title LIKE ? OR body LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend([pattern, pattern])

        where_sql = " AND ".join(where_clauses)
        count_sql = f"SELECT COUNT(*) as total FROM reminders WHERE {where_sql};"

        async with self._get_connection() as conn:
            cursor = await conn.execute(count_sql, params)
            row = await cursor.fetchone()
            return int(row["total"]) if row else 0

    async def close(self) -> None:
        pass
