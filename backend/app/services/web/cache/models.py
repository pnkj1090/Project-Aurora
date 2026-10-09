"""
Data models for the in-memory web cache.
Enforces that cache entries never store execution-specific citation IDs or execution IDs.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional
from pydantic import BaseModel, Field


class CacheScope(str, Enum):
    """
    Scope boundary for cache isolation.
    USER: Isolated to a specific user (safe default).
    PUBLIC: Eligible for global cross-user reuse only when explicitly verified user-independent.
    """
    USER = "user"
    PUBLIC = "public"


class CacheEntry(BaseModel):
    """
    Immutable in-memory web cache entry.
    
    CRITICAL INVARIANT:
    Cache entries must NOT contain execution-scoped citation IDs (e.g. src-1).
    Citation IDs are generated per-execution by the execution citation registry upon retrieval.
    """
    key: str
    scope: CacheScope = CacheScope.USER
    user_id: Optional[str] = None
    canonical_url: str
    title: Optional[str] = None
    domain: Optional[str] = None
    content_bytes: Optional[bytes] = None
    extracted_text: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    status_code: int = 200
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at


@dataclass(frozen=True)
class CacheStats:
    """Telemetry counters for cache hits, misses, and evictions."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    current_size: int = 0
