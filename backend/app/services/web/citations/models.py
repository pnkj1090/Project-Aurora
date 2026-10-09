"""
Phase 5K.4 — Citation data models.

Citation: Internal execution-scoped citation record.
CitationOut: Public API presentation model.

Both models are constructed exclusively from validated web-tool result data.
The model LLM cannot create, modify, or override citation records.
"""
from typing import Optional
from pydantic import BaseModel


class Citation(BaseModel):
    """
    Execution-scoped citation record, constructed from a validated web-tool result.

    Fields:
        id:             Execution-unique opaque citation ID.
                        Format: c_<8-char-hex-namespace>_<counter>
                        The namespace is cryptographically random per CitationRegistry
                        instance and does NOT expose the raw execution_id.
        url:            Phase 5K.1 canonical URL (validated and normalized).
        title:          Page/result title, max 500 chars. Server-validated.
                        None if not provided by validated tool result.
        domain:         Hostname derived exclusively from the canonical URL.
                        NEVER from caller-provided domain argument.
        published_date: ISO-8601 date string, server-validated.
                        None if not provided.
    """
    id: str
    url: str
    title: Optional[str] = None
    domain: Optional[str] = None
    published_date: Optional[str] = None


class CitationOut(BaseModel):
    """
    Public API presentation model for a citation.

    Identical fields to Citation. Excluded:
        - Internal entity IDs (task_, cmt_, rem_, mem_, cand_, conv_, exec_, usr_, dev_)
        - Execution IDs
        - User IDs
        - Credentials / auth tokens
        - Backend paths
        - Cookies / session data
        - Authorization information

    The iOS client renders these fields as-is; it does not validate or re-fetch.
    """
    id: str
    url: str
    title: Optional[str] = None
    domain: Optional[str] = None
    published_date: Optional[str] = None
