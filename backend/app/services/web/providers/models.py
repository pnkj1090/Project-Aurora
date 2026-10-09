"""
Data models for the vendor-neutral web search provider abstraction.
Enforces strict field boundaries and zero citation/execution identity leakage.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class WebSearchResultItem(BaseModel):
    """
    Individual structured search result item from a search provider.
    
    CRITICAL INVARIANTS:
    - Contains NO citation IDs or execution-specific identifiers.
    - Contains NO internal Aurora identifiers (tsk_, cmt_, rem_, etc.).
    - URLs must be canonicalized and validated through Phase 5K.1.
    """
    title: str = Field(..., description="Page title, max 500 characters.")
    url: str = Field(..., description="Canonical public HTTP/HTTPS URL.")
    snippet: Optional[str] = Field(None, description="Short snippet or summary, max 2000 characters.")
    domain: Optional[str] = Field(None, description="Domain/hostname of the source.")
    published_date: Optional[str] = Field(None, description="Optional ISO-8601 published date string.")


class WebSearchResponse(BaseModel):
    """
    Structured search response container returned by search providers.
    """
    query: str
    results: List[WebSearchResultItem] = Field(default_factory=list)
    total_results: int = 0
