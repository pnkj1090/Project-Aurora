"""
Outbound Privacy Boundary package for Project Aurora.
Ensures that only generalized public search intent leaves Aurora.
"""
from app.services.web.privacy.models import (
    OutboundPrivacyViolationError,
    OutboundSearchRequest,
    PrivacyBoundaryError,
    SafeOutboundSearchRequest,
)
from app.services.web.privacy.policy import (
    CREDENTIAL_PATTERNS,
    EMAIL_PATTERN,
    GPS_COORDINATES_PATTERN,
    PHONE_PATTERN,
    PRIVATE_NETWORK_PATTERNS,
    PROHIBITED_ID_PATTERNS,
    PROMPT_INJECTION_OR_LEAK_PATTERNS,
)
from app.services.web.privacy.transformer import transform_query_for_public_search
from app.services.web.privacy.validator import validate_outbound_request

__all__ = [
    "OutboundSearchRequest",
    "SafeOutboundSearchRequest",
    "PrivacyBoundaryError",
    "OutboundPrivacyViolationError",
    "transform_query_for_public_search",
    "validate_outbound_request",
    "PROHIBITED_ID_PATTERNS",
    "GPS_COORDINATES_PATTERN",
    "EMAIL_PATTERN",
    "PHONE_PATTERN",
    "CREDENTIAL_PATTERNS",
    "PRIVATE_NETWORK_PATTERNS",
    "PROMPT_INJECTION_OR_LEAK_PATTERNS",
]
