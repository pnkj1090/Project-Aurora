from enum import Enum
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

from app.services.memory.base import BaseMemoryStore
from app.services.memory.retriever import BaseMemoryRetriever, LexicalMemoryRetriever
from app.services.memory.schemas import (
    MemoryItem,
    MemoryType,
    MemorySource,
    MemoryStatus,
)

logger = logging.getLogger("aurora.memory.manager")


class DuplicateClassification(str, Enum):
    """
    Internal, read-only duplicate classification for memory candidate evaluation.
    Non-destructive invariant: POSSIBLE_DUPLICATE never triggers automated merge/delete.
    """
    EXACT_DUPLICATE = "exact_duplicate"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    NO_DUPLICATE = "no_duplicate"


class MemoryManager:
    """
    Orchestration layer for Aurora's personal memory system.
    Enforces validation, normalization, source/status policies, deduplication,
    conflict detection, user isolation, and retrieval integration.
    """

    def __init__(
        self,
        store: BaseMemoryStore,
        retriever: Optional[BaseMemoryRetriever] = None
    ):
        self.store = store
        self.retriever = retriever or LexicalMemoryRetriever(store=self.store)

    def normalize_content(self, content: str) -> str:
        """Strip excess whitespace and normalize common formatting."""
        return " ".join(content.strip().split())

    def normalize_tags(self, tags: Optional[List[str]]) -> List[str]:
        """Normalize, lowercase, and deduplicate tag lists."""
        if not tags:
            return []
        cleaned = set()
        for t in tags:
            stripped = t.strip().lower().lstrip("#")
            if stripped:
                cleaned.add(stripped)
        return sorted(list(cleaned))

    async def classify_duplicate(
        self,
        candidate: Any,
        existing_or_type: Any = None,
        tags: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> Any:
        """
        Read-only classification of candidate content against existing active memories,
        or direct pairwise comparison between two MemoryItem instances.
        Strictly non-destructive: never alters, replaces, or merges existing records.
        """
        # Direct pairwise comparison between two MemoryItem instances
        if isinstance(candidate, MemoryItem) and isinstance(existing_or_type, MemoryItem):
            m1 = candidate
            m2 = existing_or_type
            if m1.type != m2.type:
                return DuplicateClassification.NO_DUPLICATE

            c1 = self.normalize_content(m1.content).lower()
            c2 = self.normalize_content(m2.content).lower()
            if c1 == c2:
                return DuplicateClassification.EXACT_DUPLICATE

            toks1 = set(c1.split())
            toks2 = set(c2.split())
            union = toks1 | toks2
            intersection = toks1 & toks2
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard >= 0.75:
                return DuplicateClassification.POSSIBLE_DUPLICATE

            tags1 = set(self.normalize_tags(m1.tags))
            tags2 = set(self.normalize_tags(m2.tags))
            shared_tags = tags1.intersection(tags2)
            if shared_tags:
                min_len = min(len(toks1), len(toks2))
                overlap = len(intersection) / max(1, min_len)
                if overlap >= 0.50:
                    return DuplicateClassification.POSSIBLE_DUPLICATE
                if m1.type == MemoryType.PREFERENCE:
                    return DuplicateClassification.POSSIBLE_DUPLICATE

            return DuplicateClassification.NO_DUPLICATE

        # Otherwise, candidate against store lookup
        content = candidate.content if isinstance(candidate, MemoryItem) else str(candidate)
        mem_type = candidate.type if isinstance(candidate, MemoryItem) else existing_or_type
        cand_tags = candidate.tags if isinstance(candidate, MemoryItem) else tags

        normalized_content = self.normalize_content(content)
        if not normalized_content:
            return DuplicateClassification.NO_DUPLICATE, None

        cleaned_tags = self.normalize_tags(cand_tags)
        existing_memories = await self.store.list_memories(
            types=[mem_type] if mem_type else None,
            statuses=[MemoryStatus.ACTIVE],
            include_inactive=False,
            include_expired=False,
            user_id=user_id,
            allow_legacy_unscoped=True if user_id is None else False,
            limit=100,
        )

        target_lower = normalized_content.lower()
        target_tokens = set(target_lower.split())

        # 1. Exact duplicate check: same type and identical normalized content
        for existing in existing_memories:
            if existing.content.lower() == target_lower:
                return DuplicateClassification.EXACT_DUPLICATE, existing

        # 2. Possible duplicate check
        for existing in existing_memories:
            exist_lower = existing.content.lower()
            exist_tokens = set(exist_lower.split())

            union = target_tokens | exist_tokens
            intersection = target_tokens & exist_tokens
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard >= 0.75:
                return DuplicateClassification.POSSIBLE_DUPLICATE, existing

            shared_tags = set(existing.tags).intersection(set(cleaned_tags))
            if shared_tags:
                min_len = min(len(target_tokens), len(exist_tokens))
                token_overlap = len(intersection) / max(1, min_len)
                if token_overlap >= 0.50:
                    return DuplicateClassification.POSSIBLE_DUPLICATE, existing
                if mem_type == MemoryType.PREFERENCE:
                    return DuplicateClassification.POSSIBLE_DUPLICATE, existing

        return DuplicateClassification.NO_DUPLICATE, None

    async def find_duplicate_or_conflict(
        self,
        content: str,
        type: MemoryType,
        tags: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[Optional[MemoryItem], Optional[MemoryItem]]:
        """
        Read-only evaluation reusing MemoryManager's exact normalization,
        duplicate matching, and preference/potential-fact conflict detection rules.
        Returns:
            (exact_duplicate: Optional[MemoryItem], conflicting_item: Optional[MemoryItem])
        """
        normalized_content = self.normalize_content(content)
        if not normalized_content:
            return None, None

        cleaned_tags = self.normalize_tags(tags)
        existing_memories = await self.store.list_memories(
            types=[type],
            statuses=[MemoryStatus.ACTIVE],
            include_inactive=False,
            include_expired=False,
            user_id=user_id,
            allow_legacy_unscoped=True if user_id is None else False,
            limit=100,
        )

        target_lower = normalized_content.lower()
        exact_dup: Optional[MemoryItem] = None
        conflict_item: Optional[MemoryItem] = None

        for existing in existing_memories:
            if existing.content.lower() == target_lower:
                exact_dup = existing
                break

        # Preference conflict: shares tags with existing active preference but differing content
        if type == MemoryType.PREFERENCE and cleaned_tags and not exact_dup:
            for existing in existing_memories:
                shared_tags = set(existing.tags).intersection(set(cleaned_tags))
                if shared_tags and existing.content.lower() != target_lower:
                    conflict_item = existing
                    break

        # Potential Fact conflict: shares key topic/entity tags with existing active fact but differing content
        if type == MemoryType.FACT and cleaned_tags and not exact_dup:
            for existing in existing_memories:
                shared_tags = set(existing.tags).intersection(set(cleaned_tags))
                if shared_tags and existing.content.lower() != target_lower:
                    conflict_item = existing
                    break

        return exact_dup, conflict_item

    async def create_memory(
        self,
        content: str,
        type: MemoryType = MemoryType.EXPLICIT,
        importance: int = 3,
        confidence: float = 1.0,
        source: MemorySource = MemorySource.USER_EXPLICIT,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        tags: Optional[List[str]] = None,
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[MemoryItem, bool]:
        """
        Creates or updates a memory item with deduplication and conflict handling.
        Returns (MemoryItem, is_created: bool).
        """
        normalized_content = self.normalize_content(content)
        if not normalized_content:
            raise ValueError("Memory content cannot be empty.")

        cleaned_tags = self.normalize_tags(tags)
        clamped_importance = max(1, min(5, importance))
        clamped_confidence = max(0.0, min(1.0, confidence))

        item_meta = dict(metadata or {})
        if user_id and user_id.strip():
            item_meta["user_id"] = user_id.strip()

        # 1. Deduplication Check: Look for existing active memory with identical type and content
        existing_memories = await self.store.list_memories(
            types=[type],
            statuses=[MemoryStatus.ACTIVE],
            include_inactive=False,
            include_expired=False,
            user_id=user_id,
            allow_legacy_unscoped=True if user_id is None else False,
            limit=100
        )

        target_lower = normalized_content.lower()
        for existing in existing_memories:
            if existing.content.lower() == target_lower:
                logger.info("Deduplicating memory (updating existing %s)", existing.id)
                # Merge tags and take higher importance
                merged_tags = sorted(list(set(existing.tags + cleaned_tags)))
                existing.tags = merged_tags
                existing.importance = max(existing.importance, clamped_importance)
                existing.confidence = max(existing.confidence, clamped_confidence)
                if expires_at:
                    existing.expires_at = expires_at
                if item_meta:
                    existing.metadata.update(item_meta)

                saved = await self.store.put(existing)
                return saved, False

        # 2. Conflict Handling for Preferences:
        # If a preference shares exact key tags with an existing active preference,
        # update the existing preference to avoid accumulating contradictory guidance.
        if type == MemoryType.PREFERENCE and cleaned_tags:
            for existing in existing_memories:
                shared_tags = set(existing.tags).intersection(set(cleaned_tags))
                if shared_tags:
                    logger.info("Preference conflict detected on tags %s (updating existing %s)", shared_tags, existing.id)
                    existing.content = normalized_content
                    existing.importance = clamped_importance
                    existing.tags = sorted(list(set(existing.tags + cleaned_tags)))
                    if item_meta:
                        existing.metadata.update(item_meta)
                    saved = await self.store.put(existing)
                    return saved, False

        # 3. Create fresh MemoryItem
        new_memory = MemoryItem(
            type=type,
            content=normalized_content,
            importance=clamped_importance,
            confidence=clamped_confidence,
            source=source,
            status=status,
            tags=cleaned_tags,
            expires_at=expires_at,
            metadata=item_meta,
            is_active=True,
        )

        saved = await self.store.put(new_memory)
        logger.info("Created new memory: %s (type: %s)", saved.id, saved.type.value)
        return saved, True

    async def update_memory(
        self,
        memory_id: str,
        content: Optional[str] = None,
        importance: Optional[int] = None,
        tags: Optional[List[str]] = None,
        status: Optional[MemoryStatus] = None,
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = False,
    ) -> Optional[MemoryItem]:
        """Update fields on an existing memory item."""
        existing = await self.store.get(memory_id)
        if not existing:
            return None

        if user_id is not None:
            clean_uid = user_id.strip()
            if existing.user_id != clean_uid:
                if not (allow_legacy_unscoped and existing.user_id is None):
                    return None

        if content is not None:
            normalized = self.normalize_content(content)
            if not normalized:
                raise ValueError("Memory content cannot be empty.")
            existing.content = normalized

        if importance is not None:
            existing.importance = max(1, min(5, importance))

        if tags is not None:
            existing.tags = self.normalize_tags(tags)

        if status is not None:
            existing.status = status
            existing.is_active = (status == MemoryStatus.ACTIVE)

        if expires_at is not None:
            existing.expires_at = expires_at

        if metadata is not None:
            existing.metadata.update(metadata)

        return await self.store.put(existing)

    async def get_memory(
        self,
        memory_id: str,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = False,
    ) -> Optional[MemoryItem]:
        """Retrieve memory by ID with optional user-scoping."""
        item = await self.store.get(memory_id)
        if not item:
            return None
        if user_id is not None:
            clean_uid = user_id.strip()
            if item.user_id != clean_uid:
                if not (allow_legacy_unscoped and item.user_id is None):
                    return None
        return item

    async def delete_memory(
        self,
        memory_id: str,
        soft: bool = True,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = False,
    ) -> bool:
        """Delete or archive memory by ID with optional user-scoping."""
        if user_id is not None:
            clean_uid = user_id.strip()
            existing = await self.store.get(memory_id)
            if not existing:
                return False
            if existing.user_id != clean_uid:
                if not (allow_legacy_unscoped and existing.user_id is None):
                    return False
        return await self.store.delete(memory_id, soft=soft)

    # ── Strict User-Scoped Domain Methods ──────────────────────────────────────

    async def retrieve_memories(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        types: Optional[List[MemoryType]] = None,
        min_importance: int = 1,
        now: Optional[datetime] = None,
        allow_legacy_unscoped: bool = False,
    ) -> List[MemoryItem]:
        """
        Retrieve relevant active memories strictly scoped to user_id.
        Fails closed (raises ValueError) if user_id is empty, None, or whitespace.
        Never silently falls back to DEFAULT_LOCAL_USER_ID or unscoped retrieval.
        """
        clean_uid = (user_id or "").strip() if isinstance(user_id, str) else ""
        if not clean_uid:
            raise ValueError("user_id must be a non-empty string for user-scoped retrieval.")
        return await self.retriever.retrieve(
            query=query,
            limit=limit,
            types=types,
            min_importance=min_importance,
            user_id=clean_uid,
            allow_legacy_unscoped=allow_legacy_unscoped,
            now=now,
        )

    async def retrieve_memories_unscoped_internal(
        self,
        query: str,
        limit: int = 5,
        types: Optional[List[MemoryType]] = None,
        min_importance: int = 1,
        now: Optional[datetime] = None,
    ) -> List[MemoryItem]:
        """
        Dedicated internal legacy test helper.
        Permitted ONLY for pre-existing legacy test fixtures without user identity context.
        NEVER to be called from production API or conversation context provider.
        """
        logger.warning("Unscoped memory retrieval invoked via internal legacy test helper")
        return await self.retriever.retrieve(
            query=query,
            limit=limit,
            types=types,
            min_importance=min_importance,
            user_id=None,
            allow_legacy_unscoped=True,
            now=now,
        )

    async def list_memories_for_user(
        self,
        user_id: str,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        limit: int = 100,
        offset: int = 0,
        allow_legacy_unscoped: bool = False,
    ) -> List[MemoryItem]:
        """Strict user-scoped listing: fails closed if user_id is empty or missing."""
        clean_uid = (user_id or "").strip()
        if not clean_uid:
            raise ValueError("user_id must be a non-empty string.")
        return await self.store.list_memories(
            types=types,
            statuses=statuses,
            source=source,
            tags=tags,
            query=query,
            include_inactive=include_inactive,
            include_expired=include_expired,
            user_id=clean_uid,
            allow_legacy_unscoped=allow_legacy_unscoped,
            limit=limit,
            offset=offset,
        )

    async def count_memories_for_user(
        self,
        user_id: str,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        allow_legacy_unscoped: bool = False,
    ) -> int:
        """Strict user-scoped counting: fails closed if user_id is empty or missing."""
        clean_uid = (user_id or "").strip()
        if not clean_uid:
            raise ValueError("user_id must be a non-empty string.")
        return await self.store.count(
            types=types,
            statuses=statuses,
            source=source,
            tags=tags,
            query=query,
            include_inactive=include_inactive,
            include_expired=include_expired,
            user_id=clean_uid,
            allow_legacy_unscoped=allow_legacy_unscoped,
        )

    async def get_memory_for_user(
        self,
        user_id: str,
        memory_id: str,
        allow_legacy_unscoped: bool = False,
    ) -> Optional[MemoryItem]:
        """Strict user-scoped detail retrieval: returns None for foreign records."""
        clean_uid = (user_id or "").strip()
        if not clean_uid:
            raise ValueError("user_id must be a non-empty string.")
        item = await self.store.get(memory_id)
        if not item:
            return None
        if item.user_id == clean_uid:
            return item
        if allow_legacy_unscoped and item.user_id is None:
            return item
        return None

    async def delete_memory_for_user(
        self,
        user_id: str,
        memory_id: str,
        soft: bool = True,
        allow_legacy_unscoped: bool = False,
    ) -> bool:
        """Strict user-scoped deletion: rejects operations on foreign records."""
        clean_uid = (user_id or "").strip()
        if not clean_uid:
            raise ValueError("user_id must be a non-empty string.")
        existing = await self.get_memory_for_user(
            clean_uid, memory_id, allow_legacy_unscoped=allow_legacy_unscoped
        )
        if not existing:
            return False
        return await self.store.delete(memory_id, soft=soft)

    async def create_memory_for_user(
        self,
        user_id: str,
        content: str,
        type: MemoryType = MemoryType.EXPLICIT,
        importance: int = 3,
        confidence: float = 1.0,
        source: MemorySource = MemorySource.USER_EXPLICIT,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        tags: Optional[List[str]] = None,
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[MemoryItem, bool]:
        """Strict user-scoped memory creation: stamps server-controlled user_id."""
        clean_uid = (user_id or "").strip()
        if not clean_uid:
            raise ValueError("user_id must be a non-empty string.")
        meta = dict(metadata or {})
        meta["user_id"] = clean_uid
        return await self.create_memory(
            content=content,
            type=type,
            importance=importance,
            confidence=confidence,
            source=source,
            status=status,
            tags=tags,
            expires_at=expires_at,
            metadata=meta,
            user_id=clean_uid,
        )

    # ── Legacy / Internal Domain Helpers ───────────────────────────────────────

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
        limit: int = 100,
        offset: int = 0,
    ) -> List[MemoryItem]:
        """Legacy list memories with filter criteria. Used by legacy test fixtures."""
        return await self.store.list_memories(
            types=types,
            statuses=statuses,
            source=source,
            tags=tags,
            query=query,
            include_inactive=include_inactive,
            include_expired=include_expired,
            user_id=user_id,
            allow_legacy_unscoped=True if user_id is None else False,
            limit=limit,
            offset=offset,
        )

    async def count_memories(
        self,
        types: Optional[List[MemoryType]] = None,
        statuses: Optional[List[MemoryStatus]] = None,
        source: Optional[MemorySource] = None,
        tags: Optional[List[str]] = None,
        query: Optional[str] = None,
        include_inactive: bool = False,
        include_expired: bool = False,
        user_id: Optional[str] = None,
    ) -> int:
        """Legacy count memories matching filter criteria. Used by legacy test fixtures."""
        return await self.store.count(
            types=types,
            statuses=statuses,
            source=source,
            tags=tags,
            query=query,
            include_inactive=include_inactive,
            include_expired=include_expired,
            user_id=user_id,
            allow_legacy_unscoped=True if user_id is None else False,
        )

    async def cleanup_expired(self) -> int:
        """Scan and soft-delete/archive all expired memories."""
        all_memories = await self.store.list_memories(
            include_inactive=False,
            include_expired=True,
            allow_legacy_unscoped=True,
            limit=1000
        )
        archived_count = 0
        for m in all_memories:
            if m.is_expired:
                await self.store.delete(m.id, soft=True)
                archived_count += 1
        return archived_count
