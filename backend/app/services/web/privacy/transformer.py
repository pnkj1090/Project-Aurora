"""
Outbound search query transformation and generalization module.
Transforms raw intent into the minimum generalized public search query.
"""
import re
from typing import Any, Dict, Optional

from app.services.web.privacy.policy import (
    COARSE_GPS_COORDINATES_PATTERN,
    GPS_COORDINATES_PATTERN,
    PROHIBITED_ID_PATTERNS,
)


def transform_query_for_public_search(
    raw_query: str,
    user_context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Transforms and generalizes a search query before validation.
    
    Operations:
    1. Replaces exact GPS coordinates with coarse location (e.g. city) if available,
       or removes the raw coordinate numbers.
    2. Removes internal task/entity ID references.
    3. Strips internal prefixes and contextual framing.
    4. Normalizes whitespace and punctuation.
    
    If the query cannot be safely transformed, returns the sanitized remnant
    which will be validated by the fail-closed validator.
    """
    if not raw_query or not isinstance(raw_query, str):
        return ""

    query = raw_query.strip()
    ctx = user_context or {}

    # 1. Coordinate generalization
    coarse_location = ctx.get("city") or ctx.get("region") or ctx.get("country")
    if GPS_COORDINATES_PATTERN.search(query):
        if coarse_location:
            query = GPS_COORDINATES_PATTERN.sub(str(coarse_location), query)
        else:
            query = GPS_COORDINATES_PATTERN.sub("", query)

    if COARSE_GPS_COORDINATES_PATTERN.search(query):
        if coarse_location:
            query = COARSE_GPS_COORDINATES_PATTERN.sub(str(coarse_location), query)
        else:
            query = COARSE_GPS_COORDINATES_PATTERN.sub("", query)

    # 2. Internal entity reference removal
    # Matches both composite mentions like "task tsk_12345" and standalone IDs like "tsk_12345"
    # Guaranteed to remove the entire token and any preceding entity noun, leaving zero numeric/ID residue
    entity_reference_pattern = re.compile(
        r"\b(?:(?:task|commitment|reminder|memory|candidate|conversation|execution|session|user|device)\s+)?"
        r"(?:task|tsk|commitment|cmt|reminder|rem|memory|mem|candidate|cand|conversation|conv|execution|exec|usr|user|dev|device|asset|sess|inst)_[a-zA-Z0-9_-]+\b",
        re.IGNORECASE,
    )
    query = entity_reference_pattern.sub("", query)

    # 3. Clean common private contextual framing prefixes
    contextual_prefixes = [
        r"^(based on my calendar|from my calendar|in my calendar)\s*,?\s*",
        r"^(based on my tasks|from my tasks|in my tasks)\s*,?\s*",
        r"^(based on my reminders|from my reminders|in my reminders)\s*,?\s*",
        r"^(based on my notes|from my notes|in my notes)\s*,?\s*",
        r"^(based on my memories|from my memories|in my memories)\s*,?\s*",
    ]
    for cp in contextual_prefixes:
        query = re.sub(cp, "", query, flags=re.IGNORECASE)

    # 4. Normalize spaces and redundant punctuation
    query = re.sub(r"\s+", " ", query).strip(" ,;:-")

    return query
