"""
Phase 5K.4 — Execution-scoped CitationRegistry.

Responsibilities:
    - Assign execution-unique citation IDs.
    - Deduplicate by canonical URL within an execution.
    - Derive authoritative domain from canonical URL hostname.
    - Format the available-sources block for final synthesis using
      Phase 5K.2-compatible attribute-value escaping.

Security boundaries:
    - CitationRegistry performs NO SSRF validation, DNS resolution,
      TLS verification, or network access of any kind.
    - Security authority remains exclusively with SafeWebReaderService
      + Phase 5K.1 + Phase 5K.2. URLs arriving here are already fully
      validated by that pipeline.
    - Citation IDs are opaque and non-predictable across executions.
    - The raw execution_id is NEVER exposed via citation IDs.
    - domain is derived from the canonical URL hostname, never from
      caller-provided domain arguments.
"""
import re
import secrets
import urllib.parse
from typing import Dict, List, Optional

from app.services.web.citations.models import Citation, CitationOut


# Maximum character lengths for server-controlled string fields
_MAX_TITLE_CHARS = 500
_MAX_PUBLISHED_DATE_CHARS = 30

# Regex for characters that must be escaped in XML attribute values.
# Escapes: &, ", <, >, '
_XML_ESCAPE_TABLE: Dict[str, str] = {
    "&": "&amp;",
    '"': "&quot;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&apos;",
}

# Internal identifier patterns that must never appear in citation metadata
_INTERNAL_ID_PATTERNS = re.compile(
    r"\b(task_|tsk_|cmt_|rem_|mem_|cand_|conv_|exec_|usr_|dev_)[A-Za-z0-9_\-]+",
    re.IGNORECASE,
)


def _escape_xml_attr(value: str) -> str:
    """
    Escape a string for safe insertion as an XML attribute value.
    Reuses the Phase 5K.2 hardened approach: escape all five XML
    special characters so untrusted values cannot break attribute structure.
    """
    result = []
    for ch in value:
        result.append(_XML_ESCAPE_TABLE.get(ch, ch))
    return "".join(result)


def _truncate(value: Optional[str], max_chars: int) -> Optional[str]:
    """Return value truncated to max_chars, or None if value is None/empty."""
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:max_chars]


def _derive_domain_from_url(canonical_url: str) -> Optional[str]:
    """
    Derive the authoritative domain (hostname) from a canonical URL.
    The caller-provided domain is NEVER used.
    """
    try:
        parsed = urllib.parse.urlsplit(canonical_url)
        hostname = parsed.hostname  # Returns lowercase hostname, strips brackets from IPv6
        return hostname if hostname else None
    except Exception:
        return None


def _contains_internal_id(value: str) -> bool:
    """Return True if value contains any internal Aurora identifier pattern."""
    return bool(_INTERNAL_ID_PATTERNS.search(value))


class CitationRegistry:
    """
    Execution-scoped, in-memory citation registry.

    One CitationRegistry instance is created per AgentExecutionState.
    It is attached via Pydantic PrivateAttr and is never serialized.

    Identity guarantees:
        same canonical URL + same execution  => same citation ID
        same canonical URL + different execution => different citation ID
        (guaranteed by per-instance cryptographically random namespace)

    NOT a Pydantic model — intentionally a plain Python class to prevent
    accidental Pydantic schema inclusion or serialization.
    """

    def __init__(self) -> None:
        # Cryptographically random 8-char hex namespace, unique per instance.
        # Never equals raw execution_id. Non-predictable across executions.
        self._namespace: str = secrets.token_hex(4)  # 4 bytes => 8 hex chars
        self._counter: int = 0
        self._citations_by_id: Dict[str, Citation] = {}
        self._id_by_canonical_url: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self) -> str:
        self._counter += 1
        return f"c_{self._namespace}_{self._counter}"

    def _canonicalize(self, url: str) -> Optional[str]:
        """
        Canonicalize a URL using Phase 5K.1 normalize_and_validate_url.
        Returns the canonical URL string, or None if validation fails.
        Does NOT perform SSRF/DNS/TLS/network checks.
        """
        try:
            from app.services.web.security.url import normalize_and_validate_url
            validated = normalize_and_validate_url(url)
            return validated.canonical_url
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        url: str,
        title: Optional[str] = None,
        domain: Optional[str] = None,  # Caller-provided; ignored for Citation.domain
        published_date: Optional[str] = None,
    ) -> Optional[Citation]:
        """
        Register a URL as a citation source for this execution.

        1. Canonicalize URL via Phase 5K.1 normalize_and_validate_url.
        2. Derive domain from canonical URL hostname (ignores caller-provided domain).
        3. Deduplicate: if canonical URL already registered, return existing citation.
        4. Otherwise create new execution-scoped citation with unique ID.

        Returns None if the URL cannot be canonicalized (e.g. malformed).
        Never performs SSRF/DNS/TLS/network validation.
        """
        if not isinstance(url, str) or not url.strip():
            return None

        canonical_url = self._canonicalize(url)
        if not canonical_url:
            return None

        # Deduplication within this execution
        if canonical_url in self._id_by_canonical_url:
            existing_id = self._id_by_canonical_url[canonical_url]
            return self._citations_by_id[existing_id]

        # Derive authoritative domain from canonical URL — never from caller
        authoritative_domain = _derive_domain_from_url(canonical_url)

        # Sanitize title and published_date
        safe_title = _truncate(title, _MAX_TITLE_CHARS)
        safe_date = _truncate(published_date, _MAX_PUBLISHED_DATE_CHARS)

        # Screen for internal identifier leakage in string fields
        if safe_title and _contains_internal_id(safe_title):
            safe_title = None
        if safe_date and _contains_internal_id(safe_date):
            safe_date = None

        # Create new citation
        citation_id = self._next_id()
        citation = Citation(
            id=citation_id,
            url=canonical_url,
            title=safe_title,
            domain=authoritative_domain,
            published_date=safe_date,
        )
        self._citations_by_id[citation_id] = citation
        self._id_by_canonical_url[canonical_url] = citation_id
        return citation

    def get(self, citation_id: str) -> Optional[Citation]:
        """Return the citation for the given ID, or None if not in this execution's registry."""
        return self._citations_by_id.get(citation_id)

    def resolve_ids(self, ids: List[str]) -> List[Citation]:
        """
        Resolve a list of citation ID strings against this execution's registry.
        Returns only IDs that exist in this registry, in first-appearance order,
        deduplicated. Unknown, fabricated, or cross-execution IDs are silently excluded.
        """
        seen: set = set()
        result: List[Citation] = []
        for cid in ids:
            if cid not in seen:
                seen.add(cid)
                citation = self._citations_by_id.get(cid)
                if citation is not None:
                    result.append(citation)
        return result

    def all_citations(self) -> List[Citation]:
        """Return all registered citations in registration order."""
        return list(self._citations_by_id.values())

    def is_empty(self) -> bool:
        """Return True if no citations have been registered in this execution."""
        return len(self._citations_by_id) == 0

    def format_available_sources(self) -> str:
        """
        Produce the available-sources block for final synthesis prompt augmentation.

        Uses Phase 5K.2 hardened XML attribute-value escaping so that untrusted
        values (titles, domains, URLs) cannot break the block structure via
        quotes, < / >, &, or attribute injection.

        This block is DATA ONLY — it must be passed to the model clearly marked
        as data, not as instructions. It contains:
            - citation_id (server-generated, safe)
            - domain (derived from canonical URL, server-controlled)
            - url (canonical, validated)
            - title (optional, truncated, escaped)

        It does NOT contain:
            - internal entity IDs
            - execution IDs
            - user IDs
            - credentials / auth tokens
            - backend paths
            - cookies / session data
        """
        citations = self.all_citations()
        if not citations:
            return ""

        lines = ["<available_sources>"]
        for c in citations:
            # All attribute values are XML-escaped using the Phase 5K.2 approach
            escaped_id = _escape_xml_attr(c.id)
            escaped_domain = _escape_xml_attr(c.domain or "")
            escaped_url = _escape_xml_attr(c.url)
            title_text = _escape_xml_attr(c.title) if c.title else ""

            lines.append(
                f'  <source citation_id="{escaped_id}" domain="{escaped_domain}" url="{escaped_url}">'
                f"{title_text}</source>"
            )
        lines.append("</available_sources>")
        return "\n".join(lines)

    def to_citation_out_list(self) -> List[CitationOut]:
        """Convert all registered citations to CitationOut API presentation models."""
        return [
            CitationOut(
                id=c.id,
                url=c.url,
                title=c.title,
                domain=c.domain,
                published_date=c.published_date,
            )
            for c in self.all_citations()
        ]

    @property
    def namespace(self) -> str:
        """The execution-specific random namespace (read-only, for testing purposes)."""
        return self._namespace
