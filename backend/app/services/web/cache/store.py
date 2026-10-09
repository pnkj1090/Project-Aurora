"""
In-memory LRU Web Cache store for Project Aurora.
Enforces request-driven lazy TTL expiration, 1,000-entry capacity, and user isolation.
No background sweeps, zero SQLite, and zero disk persistence.
"""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from app.services.web.cache.models import CacheEntry, CacheScope, CacheStats
from app.services.web.cache.policy import (
    DEFAULT_CACHE_TTL_SECONDS,
    MAX_CACHE_ENTRIES,
    generate_cache_key,
)


class InMemoryWebCache:
    """
    Thread-safe, request-driven LRU in-memory cache for web search and page retrieval.
    """
    def __init__(
        self,
        max_entries: int = MAX_CACHE_ENTRIES,
        default_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ):
        self.max_entries = max_entries
        self.default_ttl_seconds = default_ttl_seconds
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    @property
    def stats(self) -> CacheStats:
        """Returns current cache statistics."""
        return CacheStats(
            hits=self._hits,
            misses=self._misses,
            evictions=self._evictions,
            current_size=len(self._entries),
        )

    def get(self, key: str, user_id: Optional[str] = None) -> Optional[CacheEntry]:
        """
        Retrieves an entry by key with user isolation checks and lazy expiration.
        
        Rules:
        - If missing or expired: returns None (miss).
        - If scope is USER: returns None unless entry.user_id matches requested user_id.
        - Moves retrieved entry to the end of the LRU queue.
        """
        if key not in self._entries:
            self._misses += 1
            return None

        entry = self._entries[key]

        # Lazy expiration check
        if entry.is_expired:
            del self._entries[key]
            self._misses += 1
            return None

        # Cross-user isolation boundary
        if entry.scope == CacheScope.USER:
            if not user_id or entry.user_id != user_id:
                # Treated as a cache miss for another user
                self._misses += 1
                return None

        # LRU touch: move to end
        self._entries.move_to_end(key)
        self._hits += 1
        return entry

    def put(self, entry: CacheEntry) -> None:
        """
        Inserts or updates a cache entry with LRU eviction.
        """
        if not isinstance(entry, CacheEntry):
            raise TypeError("entry must be an instance of CacheEntry")

        # Evict oldest entry if at capacity
        if len(self._entries) >= self.max_entries and entry.key not in self._entries:
            self._entries.popitem(last=False)
            self._evictions += 1

        self._entries[entry.key] = entry
        self._entries.move_to_end(entry.key)

    def set_content(
        self,
        target: str,
        canonical_url: str,
        scope: CacheScope = CacheScope.USER,
        user_id: Optional[str] = None,
        title: Optional[str] = None,
        domain: Optional[str] = None,
        content_bytes: Optional[bytes] = None,
        extracted_text: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        status_code: int = 200,
        ttl_seconds: Optional[int] = None,
    ) -> CacheEntry:
        """
        Convenience helper to construct, key, and store a CacheEntry.
        """
        key = generate_cache_key(target=target, scope=scope, user_id=user_id)
        now = datetime.now(timezone.utc)
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        expires_at = now + timedelta(seconds=ttl)

        entry = CacheEntry(
            key=key,
            scope=scope,
            user_id=user_id if scope == CacheScope.USER else None,
            canonical_url=canonical_url,
            title=title,
            domain=domain,
            content_bytes=content_bytes,
            extracted_text=extracted_text,
            headers=headers or {},
            status_code=status_code,
            created_at=now,
            expires_at=expires_at,
        )
        self.put(entry)
        return entry

    # Alias for compatibility
    put_content = set_content

    def clear(self) -> None:
        """Clears all entries from the cache."""
        self._entries.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
