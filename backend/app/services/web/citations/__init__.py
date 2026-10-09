"""
Phase 5K.4 — Execution-scoped citation registry and models.

Provides execution-unique citation identity, deduplication,
and authoritative citation metadata construction exclusively
from validated web-tool results.
"""
from app.services.web.citations.models import Citation, CitationOut
from app.services.web.citations.registry import CitationRegistry

__all__ = [
    "Citation",
    "CitationOut",
    "CitationRegistry",
]
