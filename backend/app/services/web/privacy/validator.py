"""
Post-transformation outbound privacy validator for Project Aurora.
Validates the exact outbound representation immediately before provider dispatch.
Fails closed on any privacy violation, internal ID leakage, or credential exposure.
"""
from typing import Any, Dict, Optional, Set

from app.services.web.privacy.models import (
    OutboundPrivacyViolationError,
    OutboundSearchRequest,
    SafeOutboundSearchRequest,
)
from app.services.web.privacy.policy import (
    COARSE_GPS_COORDINATES_PATTERN,
    CREDENTIAL_PATTERNS,
    EMAIL_PATTERN,
    GPS_COORDINATES_PATTERN,
    PHONE_PATTERN,
    PRIVATE_NETWORK_PATTERNS,
    PROHIBITED_ID_PATTERNS,
    PROMPT_INJECTION_OR_LEAK_PATTERNS,
)
from app.services.web.privacy.transformer import transform_query_for_public_search

# Phrases that indicate residual noise without legitimate public search intent
_MEANINGLESS_QUERY_PHRASES: Set[str] = {
    "look up", "find", "search", "show me", "what is", "help with",
    "tell me about", "find information on", "information about",
    "the task", "the commitment", "the reminder", "the memory",
    "the conversation", "the user", "the device", "task",
    "commitment", "reminder", "memory", "conversation", "user", "device",
}


def validate_outbound_request(
    request: OutboundSearchRequest,
) -> SafeOutboundSearchRequest:
    """
    Validates that only the minimum generalized public search intent leaves Aurora.
    
    Pipeline order:
    1. Structural validation & narrow pre-check:
       Rejects ungeneralizable secrets/credentials (Bearer tokens, API keys, cookie headers)
       that must never be processed as outbound search intent.
    2. Privacy transformation & generalization (transformer.py):
       Converts raw user/agent intent into minimal public search intent
       (replaces coordinates with coarse region, removes internal entity references,
       and strips private contextual framing).
    3. Post-transformation validation boundary:
       Final fail-closed validation on the EXACT outbound representation immediately
       before provider dispatch. Rejects UUIDs, digests, residual IDs, coordinates,
       PII, intranet references, prompt leaks, or context leakage.
    """
    if not isinstance(request, OutboundSearchRequest):
        raise OutboundPrivacyViolationError("Invalid search request object.")

    raw_query = (request.raw_query or "").strip()
    if not raw_query:
        raise OutboundPrivacyViolationError("Outbound search query cannot be empty.")

    # Narrow pre-check: strictly for credentials and ungeneralizable secret tokens.
    # We do NOT reject coordinates or contextual framing here because they are
    # explicitly intended to be generalized by the transformation stage.
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.search(raw_query):
            raise OutboundPrivacyViolationError(
                "Outbound query contains prohibited authentication token, API key, or credential."
            )
    for pattern in PROMPT_INJECTION_OR_LEAK_PATTERNS:
        if pattern.search(raw_query):
            raise OutboundPrivacyViolationError(
                "Outbound query contains prohibited system/prompt leakage or injection pattern."
            )

    # 1. Transform and generalize raw intent
    transformed_query = transform_query_for_public_search(
        raw_query=request.raw_query,
        user_context=request.user_context,
    )

    if not transformed_query:
        raise OutboundPrivacyViolationError(
            "Outbound search query is empty after privacy transformation."
        )

    # Fail closed if transformed query has no meaningful public search intent
    if transformed_query.lower() in _MEANINGLESS_QUERY_PHRASES or len(transformed_query) < 3:
        raise OutboundPrivacyViolationError(
            "Outbound search query has no meaningful public search intent after privacy generalization."
        )

    # 2. Final Post-Transformation Validation: inspect the exact final outbound representation
    # Check for prohibited entity IDs, UUIDs, and hex digests
    for pattern in PROHIBITED_ID_PATTERNS:
        match = pattern.search(transformed_query)
        if match:
            raise OutboundPrivacyViolationError(
                f"Outbound query contains prohibited internal identifier or digest: '{match.group(0)}'."
            )

    # Check for high-precision or coarse GPS coordinates
    if GPS_COORDINATES_PATTERN.search(transformed_query) or COARSE_GPS_COORDINATES_PATTERN.search(transformed_query):
        raise OutboundPrivacyViolationError(
            "Outbound query contains prohibited raw GPS coordinates."
        )

    # Check for PII (emails and phone numbers)
    if EMAIL_PATTERN.search(transformed_query):
        raise OutboundPrivacyViolationError("Outbound query contains personal email address.")
    if PHONE_PATTERN.search(transformed_query):
        raise OutboundPrivacyViolationError("Outbound query contains personal phone number.")

    # Check for private network, localhost, and intranet references
    for pattern in PRIVATE_NETWORK_PATTERNS:
        match = pattern.search(transformed_query)
        if match:
            raise OutboundPrivacyViolationError(
                f"Outbound query contains prohibited private network/intranet reference: '{match.group(0)}'."
            )

    # Check for user-specific private context leakage if context was provided
    if request.user_context:
        _check_user_context_leakage(transformed_query, request.user_context)

    # Return immutable, safe representation
    return SafeOutboundSearchRequest(
        query=transformed_query,
        max_results=request.max_results,
        is_public_safe=True,
    )


def _check_user_context_leakage(query: str, user_context: Dict[str, Any]) -> None:
    """
    Checks if sensitive strings from user private context (contacts, memories,
    task titles, calendar events) leak into the final outbound query.
    """
    query_lower = query.lower()
    sensitive_keys = {
        "contacts", "calendar", "tasks", "commitments", "reminders",
        "memories", "proactive", "device_id", "installation_id", "user_id"
    }

    for key, val in user_context.items():
        if key in sensitive_keys and val:
            if isinstance(val, str) and len(val.strip()) >= 4:
                val_clean = val.strip().lower()
                if val_clean in query_lower:
                    raise OutboundPrivacyViolationError(
                        f"Outbound query leaks private user context ({key}): '{val}'."
                    )
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and len(item.strip()) >= 4:
                        if item.strip().lower() in query_lower:
                            raise OutboundPrivacyViolationError(
                                f"Outbound query leaks private item ({key}): '{item}'."
                            )
                    elif isinstance(item, dict):
                        for subval in item.values():
                            if isinstance(subval, str) and len(subval.strip()) >= 4:
                                if subval.strip().lower() in query_lower:
                                    raise OutboundPrivacyViolationError(
                                        f"Outbound query leaks private context ({key}): '{subval}'."
                                    )
