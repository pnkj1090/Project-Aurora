"""
Data models and exceptions for the Outbound Privacy Boundary.
Enforces the invariant: 'Only the minimum generalized public search intent may leave Aurora.'
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from app.services.web.security.exceptions import WebSecurityError


class PrivacyBoundaryError(WebSecurityError):
    """Base exception for all privacy boundary errors."""
    pass


class OutboundPrivacyViolationError(PrivacyBoundaryError):
    """Raised when an outbound search query contains prohibited internal, private, or sensitive data."""
    pass


class OutboundSearchRequest(BaseModel):
    """
    Initial search request received from internal agent/service execution.
    Subject to mandatory server-side transformation and validation.
    """
    raw_query: str
    user_id: Optional[str] = None
    max_results: int = Field(default=3, ge=1, le=5)
    user_context: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class SafeOutboundSearchRequest:
    """
    Immutable representation of a validated, generalized public search query.
    This is the only structure permitted to reach an external search provider.
    """
    query: str
    max_results: int
    is_public_safe: bool = True
