"""
Phase 5H — Proactive Assistance Foundation package exports.
"""
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveStatus,
    ProactiveTrigger,
    ProactiveAction,
    ProactivePriority,
    DEFAULT_PROACTIVE_TTL_SECONDS,
    MAX_PROACTIVE_CANDIDATES_PER_USER_PER_HOUR,
    MAX_CONTEXT_CANDIDATES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ALLOWED_PROACTIVE_SOURCES,
)
from app.services.proactive.store import (
    BaseProactiveStore,
    InMemoryProactiveStore,
)
from app.services.proactive.policy import (
    ProactivePolicy,
    TRIGGER_PERMISSION_MAP,
    SOURCE_PERMISSION_MAP,
    ACTION_PERMISSION_MAP,
)
from app.services.proactive.manager import (
    ProactiveManager,
    get_proactive_manager,
    get_proactive_manager_dependency,
)

__all__ = [
    "ProactiveCandidate",
    "ProactiveStatus",
    "ProactiveTrigger",
    "ProactiveAction",
    "ProactivePriority",
    "DEFAULT_PROACTIVE_TTL_SECONDS",
    "MAX_PROACTIVE_CANDIDATES_PER_USER_PER_HOUR",
    "MAX_CONTEXT_CANDIDATES",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "ALLOWED_PROACTIVE_SOURCES",
    "BaseProactiveStore",
    "InMemoryProactiveStore",
    "ProactivePolicy",
    "TRIGGER_PERMISSION_MAP",
    "SOURCE_PERMISSION_MAP",
    "ACTION_PERMISSION_MAP",
    "ProactiveManager",
    "get_proactive_manager",
    "get_proactive_manager_dependency",
]
