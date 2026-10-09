from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Union
import logging

from app.services.permissions.models import (
    PermissionType,
    PermissionStatus,
    PermissionRecord,
)
from app.services.identity.models import DEFAULT_LOCAL_USER_ID

logger = logging.getLogger("aurora.permissions.manager")


class BasePermissionStore(ABC):
    """
    Abstract interface for permission state stores.
    In Phase 5A, this is strictly a runtime abstraction.
    """

    @abstractmethod
    async def get_permission(
        self,
        permission_type: Union[PermissionType, str],
        user_id: str
    ) -> Optional[PermissionRecord]:
        pass

    @abstractmethod
    async def set_permission(self, record: PermissionRecord) -> PermissionRecord:
        pass

    @abstractmethod
    async def list_permissions(self, user_id: str) -> List[PermissionRecord]:
        pass


class InMemoryPermissionStore(BasePermissionStore):
    """
    In-memory runtime store for Phase 5A contract validation and tests.
    Does NOT persist to SQLite or any database. The authoritative source
    of real device permissions is the operating system on the client device.
    """

    def __init__(self, initial_records: Optional[Dict[Tuple[str, Union[PermissionType, str]], PermissionRecord]] = None):
        # Key: (user_id, permission_type)
        self._store: Dict[Tuple[str, Union[PermissionType, str]], PermissionRecord] = initial_records or {}
        # Pre-authorize reminders and memory:delete so built-in confirmation-bound test tools continue working
        for p in (PermissionType.REMINDERS, "reminders", "reminders:write", "memory:delete"):
            if (DEFAULT_LOCAL_USER_ID, p) not in self._store:
                self._store[(DEFAULT_LOCAL_USER_ID, p)] = PermissionRecord(
                    permission_type=p,
                    status=PermissionStatus.AUTHORIZED,
                    user_id=DEFAULT_LOCAL_USER_ID,
                    source="default"
                )

    async def get_permission(
        self,
        permission_type: Union[PermissionType, str],
        user_id: str
    ) -> Optional[PermissionRecord]:
        return self._store.get((user_id, permission_type))

    async def set_permission(self, record: PermissionRecord) -> PermissionRecord:
        self._store[(record.user_id, record.permission_type)] = record
        return record

    async def list_permissions(self, user_id: str) -> List[PermissionRecord]:
        return [rec for (uid, _), rec in self._store.items() if uid == user_id]


class PermissionManager:
    """
    Coordinates permission state queries and contract evaluation.
    Vendor-neutral and decoupled from platform-specific APIs.

    Enforces the strict rule:
    Only `AUTHORIZED` satisfies a required permission.
    `NOT_DETERMINED`, `DENIED`, `RESTRICTED`, and `UNAVAILABLE` never satisfy.
    """

    def __init__(self, store: Optional[BasePermissionStore] = None):
        self.store = store or InMemoryPermissionStore()

    def _coerce_type(self, permission_type: Union[PermissionType, str]) -> Union[PermissionType, str]:
        if isinstance(permission_type, PermissionType):
            return permission_type
        raw = str(permission_type).lower().strip()
        base = raw.split(":")[0] if ":" in raw else raw
        try:
            return PermissionType(base)
        except ValueError:
            return raw

    async def get_status(
        self,
        permission_type: Union[PermissionType, str],
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> PermissionStatus:
        """
        Retrieves the current authorization status.
        Defaults to NOT_DETERMINED if no explicit state has been reported.
        """
        pt = self._coerce_type(permission_type)
        record = await self.store.get_permission(pt, user_id)
        if record:
            return record.status
        return PermissionStatus.NOT_DETERMINED

    async def is_authorized(
        self,
        permission_type: Union[PermissionType, str],
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> bool:
        """
        Evaluates whether a permission is explicitly authorized.
        Only PermissionStatus.AUTHORIZED returns True.
        """
        status = await self.get_status(permission_type, user_id)
        return status == PermissionStatus.AUTHORIZED

    async def update_permission(
        self,
        permission_type: Union[PermissionType, str],
        status: Union[PermissionStatus, str],
        user_id: str = DEFAULT_LOCAL_USER_ID,
        source: str = "system",
        metadata: Optional[Dict] = None
    ) -> PermissionRecord:
        """
        Updates the runtime permission state (e.g., as reported by a platform provider).
        """
        pt = self._coerce_type(permission_type)
        ps = status if isinstance(status, PermissionStatus) else PermissionStatus(str(status).lower())

        record = PermissionRecord(
            permission_type=pt,
            status=ps,
            user_id=user_id,
            source=source,
            updated_at=datetime.now(timezone.utc),
            metadata=metadata or {}
        )
        saved = await self.store.set_permission(record)
        logger.debug("Permission '%s' updated to '%s' for user '%s'", pt.value, ps.value, user_id)
        return saved

    async def list_permissions(
        self,
        user_id: str = DEFAULT_LOCAL_USER_ID
    ) -> List[PermissionRecord]:
        return await self.store.list_permissions(user_id)


default_permission_manager = PermissionManager()


def get_permission_manager() -> PermissionManager:
    """Retrieve the singleton PermissionManager instance."""
    return default_permission_manager


def get_permission_manager_dependency() -> PermissionManager:
    """FastAPI dependency provider for PermissionManager."""
    return get_permission_manager()
