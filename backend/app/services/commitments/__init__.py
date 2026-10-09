from typing import Optional
from app.core.config import settings
from app.services.commitments.models import Commitment, CommitmentStatus, CommitmentPriority
from app.services.commitments.base import BaseCommitmentStore
from app.services.commitments.store import InMemoryCommitmentStore, SQLiteCommitmentStore
from app.services.commitments.manager import CommitmentManager

_default_commitment_store: Optional[BaseCommitmentStore] = None
_default_commitment_manager: Optional[CommitmentManager] = None


def get_commitment_store(db_path: Optional[str] = None) -> BaseCommitmentStore:
    """Factory function providing the default persistent SQLite commitment store."""
    global _default_commitment_store
    if _default_commitment_store is None or db_path is not None:
        path = db_path or settings.DATABASE_PATH
        st = SQLiteCommitmentStore(db_path=path)
        if db_path is None:
            _default_commitment_store = st
        return st
    return _default_commitment_store


def get_commitment_manager(store: Optional[BaseCommitmentStore] = None) -> CommitmentManager:
    """Factory function providing the CommitmentManager singleton or instance."""
    global _default_commitment_manager
    if _default_commitment_manager is None or store is not None:
        st = store or get_commitment_store()
        mgr = CommitmentManager(store=st)
        if store is None:
            _default_commitment_manager = mgr
        return mgr
    return _default_commitment_manager


def get_commitment_manager_dependency() -> CommitmentManager:
    """Dependency provider for FastAPI route handlers with zero arguments."""
    return get_commitment_manager()


__all__ = [
    "Commitment",
    "CommitmentStatus",
    "CommitmentPriority",
    "BaseCommitmentStore",
    "InMemoryCommitmentStore",
    "SQLiteCommitmentStore",
    "CommitmentManager",
    "get_commitment_store",
    "get_commitment_manager",
    "get_commitment_manager_dependency",
]
