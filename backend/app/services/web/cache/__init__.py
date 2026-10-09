"""
Web cache package for Project Aurora.
Provides ephemeral, request-driven in-memory caching with user isolation.
"""
from app.services.web.cache.models import CacheEntry, CacheScope, CacheStats
from app.services.web.cache.policy import (
    DEFAULT_CACHE_TTL_SECONDS,
    MAX_CACHE_ENTRIES,
    determine_cache_scope,
    generate_cache_key,
)
from app.services.web.cache.store import InMemoryWebCache

__all__ = [
    "CacheEntry",
    "CacheScope",
    "CacheStats",
    "InMemoryWebCache",
    "generate_cache_key",
    "determine_cache_scope",
    "DEFAULT_CACHE_TTL_SECONDS",
    "MAX_CACHE_ENTRIES",
]
