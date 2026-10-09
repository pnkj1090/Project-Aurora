from abc import ABC, abstractmethod
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from app.services.permissions.models import PermissionType


class ContextSensitivity(str, Enum):
    """
    Architectural sensitivity classification for situational context.
    """
    PUBLIC = "public"          # General, non-identifying situational data (e.g. current time/day)
    PRIVATE = "private"        # User personal context (e.g. user memory, preferences)
    SENSITIVE = "sensitive"    # Highly protected personal context (e.g. precise location, calendar entries)


class ContextBlock(BaseModel):
    """
    Structured context snippet provided by an individual context provider.
    Extended with sensitivity classification, freshness, and permission requirements.
    """
    title: str
    content: str
    priority: int = Field(default=100, description="Lower numbers indicate higher priority for budgeting.")
    sensitivity: ContextSensitivity = Field(default=ContextSensitivity.PUBLIC)
    source: str = Field(default="system", description="Origin of this context (e.g. system, memory, device)")
    freshness: Optional[datetime] = Field(default=None, description="Timestamp when this context was generated/measured")
    expires_at: Optional[datetime] = Field(default=None, description="Optional expiry for temporal validity")
    is_user_visible: bool = Field(default=True, description="Whether this context is user-facing or internal metadata")
    required_permission: Optional[PermissionType] = Field(default=None, description="Permission required to consume this context")
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.content.strip()


class BaseContextProvider(ABC):
    """
    Abstract interface for situational context providers.
    The ContextEngine coordinates providers without knowing their underlying implementations.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this provider."""
        pass

    @property
    def priority(self) -> int:
        """Default priority ordering (lower = earlier/higher priority)."""
        return 100

    @property
    def context_type(self) -> str:
        """Semantic category of context produced by this provider."""
        return "generic"

    @property
    def source(self) -> str:
        """Declared source of the contextual data."""
        return "system"

    @property
    def sensitivity(self) -> ContextSensitivity:
        """Default sensitivity classification for context from this provider."""
        return ContextSensitivity.PUBLIC

    @property
    def required_permission(self) -> Optional[PermissionType]:
        """Optional capability permission required to access this provider's data."""
        return None

    @property
    def is_user_visible(self) -> bool:
        """Whether context from this provider is visible in user-facing diagnostics."""
        return True

    @property
    def max_chars(self) -> Optional[int]:
        """Optional provider-level character ceiling."""
        return None

    @abstractmethod
    async def get_context(
        self,
        session: Optional[Any] = None,
        query: Optional[str] = None,
        **kwargs: Any
    ) -> Optional[ContextBlock]:
        """
        Produce a ContextBlock for the current interaction or None if no relevant context exists.
        """
        pass
