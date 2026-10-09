"""
Cache policy, scoping rules, and server-side key generation.
Enforces that user-scoped caching is the safe default and keys are generated server-side.
"""
import hashlib
from typing import Optional

from app.services.web.cache.models import CacheScope

# Approved limits
DEFAULT_CACHE_TTL_SECONDS: int = 15 * 60  # 15 minutes
MAX_CACHE_ENTRIES: int = 1000


def generate_cache_key(
    target: str,
    scope: CacheScope = CacheScope.USER,
    user_id: Optional[str] = None,
) -> str:
    """
    Generates a deterministic server-side SHA-256 cache key.
    
    The model cannot provide, inspect, or configure cache keys.
    For user-scoped entries, user_id is incorporated to guarantee partition isolation.
    """
    if not target or not isinstance(target, str):
        raise ValueError("Cache target string must be non-empty.")

    normalized_target = target.strip()

    if scope == CacheScope.USER:
        if not user_id:
            raise ValueError("user_id is strictly required for user-scoped cache keys.")
        key_material = f"user:{user_id}:{normalized_target}"
    elif scope == CacheScope.PUBLIC:
        key_material = f"public:{normalized_target}"
    else:
        # Fallback to user scope
        if not user_id:
            raise ValueError("user_id is required for non-public cache keys.")
        key_material = f"user:{user_id}:{normalized_target}"

    return hashlib.sha256(key_material.encode("utf-8")).hexdigest()


def determine_cache_scope(
    is_public_content: bool,
    has_user_context: bool,
) -> CacheScope:
    """
    Determines whether retrieved web content is safe for global public caching.
    
    Safe default invariant:
    - Content is ONLY public-cacheable if explicitly verified public and non-personalized.
    - Any ambiguity, heuristic failure, or presence of user context defaults strictly to USER.
    """
    if is_public_content and not has_user_context:
        return CacheScope.PUBLIC
    return CacheScope.USER
