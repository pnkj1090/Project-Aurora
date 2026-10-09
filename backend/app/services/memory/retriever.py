from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Set

from app.services.memory.base import BaseMemoryStore
from app.services.memory.schemas import MemoryItem, MemoryType, MemoryStatus, MemorySource


TEMPORAL_KEYWORDS: Set[str] = {
    "yesterday",
    "earlier",
    "remember when",
    "last week",
    "last month",
    "last year",
    "ago",
    "previously",
    "past",
}


def calculate_freshness(item: MemoryItem, now: Optional[datetime] = None) -> float:
    """
    Deterministic freshness/recency calculation for retrieval ranking.
    Freshness is strictly a ranking factor, NOT truth validity.
    Permanent memories (expires_at is None) have an asymptotic floor of 0.50.
    Expiring memories decay smoothly from 1.00 down to 0.20 before expiration.
    Expired memories return 0.0.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    created_utc = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=timezone.utc)
    updated_utc = item.updated_at if item.updated_at.tzinfo else item.updated_at.replace(tzinfo=timezone.utc)
    t_eff = max(created_utc, updated_utc)

    # Permanent memory (expires_at is None)
    if item.expires_at is None:
        age_days = max(0.0, (current - t_eff).total_seconds() / 86400.0)
        freshness = max(0.50, 1.0 / (1.0 + 0.02 * age_days))
        return round(freshness, 4)

    # Expiring memory
    exp_utc = item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=timezone.utc)
    if current > exp_utc:
        return 0.0

    lifespan = max(1.0, (exp_utc - t_eff).total_seconds())
    remaining = max(0.0, (exp_utc - current).total_seconds())
    freshness = 0.20 + 0.80 * (remaining / lifespan)
    return round(freshness, 4)


def get_source_multiplier(source: MemorySource) -> float:
    """Provenance multiplier: user-directed memories have highest priority."""
    if source == MemorySource.USER_EXPLICIT:
        return 1.10
    return 1.00


def is_temporally_anchored(
    item: Optional[MemoryItem] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Determines if an episodic memory has a temporal anchor in its metadata/representation.
    Checks for:
    - metadata["temporal"] (dict with timeframe/qualifier or non-empty)
    - metadata["temporal_anchor"] (truthy or string/dict)
    - metadata["timeframe"] (truthy)
    """
    meta: Dict[str, Any] = {}
    if item is not None and getattr(item, "metadata", None):
        meta = item.metadata
    elif metadata is not None:
        meta = metadata

    if not meta:
        return False

    temporal = meta.get("temporal")
    if isinstance(temporal, dict):
        if temporal.get("timeframe") or temporal.get("qualifier_phrase") or temporal.get("is_temporary"):
            return True
    elif temporal:
        return True

    if meta.get("temporal_anchor"):
        return True

    if meta.get("timeframe"):
        return True

    return False


def get_semantic_type_multiplier(
    mem_type: MemoryType,
    query: Optional[str] = None,
    item: Optional[MemoryItem] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Semantic MemoryType weighting.
    - PREFERENCE: 1.15
    - FACT: 1.05
    - COMMITMENT: 1.00
    - TASK: 1.00
    - EPISODIC: 1.15 if temporally anchored according to available memory metadata/representation; 0.95 otherwise.
    - Legacy EXPLICIT: 1.00 (neutral).
    """
    if mem_type == MemoryType.PREFERENCE:
        return 1.15
    elif mem_type == MemoryType.FACT:
        return 1.05
    elif mem_type == MemoryType.COMMITMENT:
        return 1.00
    elif mem_type == MemoryType.TASK:
        return 1.00
    elif mem_type == MemoryType.EPISODIC:
        if is_temporally_anchored(item=item, metadata=metadata):
            return 1.15
        return 0.95
    elif mem_type == MemoryType.EXPLICIT:
        return 1.00
    return 1.00


def retrieval_sort_key(score: float, item: MemoryItem) -> Tuple[float, float, float, str]:
    """
    Stable, deterministic tie-breaking key:
    1. Score descending (-score)
    2. Updated_at descending (-updated_at)
    3. Created_at descending (-created_at)
    4. Memory ID ascending (item.id)
    """
    up_ts = item.updated_at.timestamp() if item.updated_at.tzinfo else item.updated_at.replace(tzinfo=timezone.utc).timestamp()
    cr_ts = item.created_at.timestamp() if item.created_at.tzinfo else item.created_at.replace(tzinfo=timezone.utc).timestamp()
    return (-score, -up_ts, -cr_ts, item.id)


class BaseMemoryRetriever(ABC):
    """
    Abstract interface for retrieving relevant memories given a query.
    Enables future drop-in vector or hybrid retrieval without altering the rest of Aurora.
    """

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        types: Optional[List[MemoryType]] = None,
        min_importance: int = 1,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = True,
        now: Optional[datetime] = None,
    ) -> List[MemoryItem]:
        """Retrieve the most relevant memories for the provided query."""
        pass


class LexicalMemoryRetriever(BaseMemoryRetriever):
    """
    Deterministic lexical and metadata memory retriever.
    Scores memories based on lexical token matching, tags, type, importance, confidence,
    freshness decay, source provenance, and semantic type modifiers.
    Enforces deterministic 4-tuple tie breaking and fail-closed user isolation.
    """

    def __init__(self, store: BaseMemoryStore):
        self.store = store

    def _calculate_relevance(
        self,
        query: str,
        item: MemoryItem,
        now: Optional[datetime] = None,
    ) -> float:
        """
        Deterministic scoring formula:
        1. Base Lexical Match Points:
           - Exact phrase match in content (+10.0)
           - Token matches in content (+3.0 per token)
           - Token matches in tags (+4.0 per tag)
           - Token matches in type value (+2.0 per token)
        2. Scaled by normalized importance (importance / 3.0)
        3. Scaled by confidence (0.1 - 1.0)
        4. Scaled by deterministic freshness factor (asymptotic floor for permanent)
        5. Scaled by source multiplier (USER_EXPLICIT=1.10, INFERRED=1.00)
        6. Scaled by semantic type multiplier (PREFERENCE=1.15, FACT=1.05, EPISODIC=0.95/1.15)
        7. Additive preference bonus (+1.5)
        If lexical match points <= 0.0, score is 0.0 (lexical primacy).
        """
        clean_query = query.strip().lower()
        content_lower = item.content.lower()
        tags_lower = [t.lower() for t in item.tags]
        type_lower = item.type.value.lower()

        tokens = [t for t in clean_query.split() if len(t) > 1]
        match_points = 0.0

        # Exact phrase match in content
        if clean_query in content_lower:
            match_points += 10.0

        # Token matches in content
        for t in tokens:
            if t in content_lower:
                match_points += 3.0
            if any(t in tag for tag in tags_lower):
                match_points += 4.0
            if t in type_lower:
                match_points += 2.0

        # If zero lexical match points, this memory is not relevant
        if match_points <= 0.0:
            return 0.0

        # Importance multiplier: 1 -> 0.33, 3 -> 1.0, 5 -> 1.67
        importance_factor = item.importance / 3.0

        # Confidence multiplier: 0.1 to 1.0
        confidence_factor = max(0.1, min(1.0, item.confidence))

        # Deterministic freshness factor
        freshness_factor = calculate_freshness(item, now)

        # Source multiplier (provenance)
        source_factor = get_source_multiplier(item.source)

        # Semantic type multiplier (episodic boosted if temporally anchored)
        type_factor = get_semantic_type_multiplier(item.type, query=query, item=item)

        # Additive preference bonus
        type_bonus = 1.5 if item.type == MemoryType.PREFERENCE else 0.0

        base_score = match_points * importance_factor * confidence_factor * freshness_factor * source_factor * type_factor
        return round(base_score + type_bonus, 4)

    async def retrieve(
        self,
        query: str,
        limit: int = 5,
        types: Optional[List[MemoryType]] = None,
        min_importance: int = 1,
        user_id: Optional[str] = None,
        allow_legacy_unscoped: bool = True,
        now: Optional[datetime] = None,
    ) -> List[MemoryItem]:
        # Retrieval Limit Contract
        if limit < 0:
            raise ValueError("limit cannot be negative")
        if limit == 0:
            return []

        effective_limit = min(limit, 20)

        clean_query = query.strip()
        if not clean_query:
            return []

        # Retrieve active candidate memories from store
        candidates = await self.store.list_memories(
            types=types,
            statuses=[MemoryStatus.ACTIVE],
            include_inactive=False,
            include_expired=False,
            user_id=user_id,
            allow_legacy_unscoped=allow_legacy_unscoped,
            limit=200,
        )

        current_time = now or datetime.now(timezone.utc)
        scored: List[Tuple[float, MemoryItem]] = []

        for item in candidates:
            # User isolation filtering
            if user_id is not None:
                item_uid = item.user_id
                if item_uid != user_id:
                    if not (allow_legacy_unscoped and item_uid is None):
                        continue
            elif not allow_legacy_unscoped:
                # user_id is None and allow_legacy_unscoped is False -> fail closed
                continue

            # Exclude expired memories at evaluation time
            if item.is_expired_at(current_time):
                continue

            if item.importance < min_importance:
                continue

            score = self._calculate_relevance(clean_query, item, current_time)
            if score > 0.0:
                scored.append((score, item))

        # Deterministic 4-tuple sorting: score desc, updated_at desc, created_at desc, id asc
        scored.sort(key=lambda x: retrieval_sort_key(x[0], x[1]))
        return [item for _, item in scored[:effective_limit]]

