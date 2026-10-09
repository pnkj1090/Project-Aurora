"""
Data models for the Safe Web Reader service and tools.
Strongly typed representations for page retrieval and web search results.
"""
from typing import List, Optional
from pydantic import BaseModel, Field

from app.services.web.providers.models import WebSearchResultItem


class WebPageReadResult(BaseModel):
    """
    Structured outcome of a securely executed web page retrieval.
    Contains strictly passive extracted text and metadata.
    Never exposes raw HTTP headers, cookies, or socket connections.
    """
    url: str = Field(..., description="Canonical validated public URL.")
    title: Optional[str] = Field(None, description="Extracted HTML title if present.")
    domain: Optional[str] = Field(None, description="Domain/hostname of the target.")
    text: str = Field(..., description="Extracted readable text content, capped at 12,000 characters.")
    character_count: int = Field(..., description="Length of the extracted text.")
    is_truncated: bool = Field(False, description="True if text exceeded length caps.")
    cached: bool = Field(False, description="True if satisfied from the user-scoped in-memory cache.")


class WebSearchToolResult(BaseModel):
    """
    Structured outcome of a web search operation.
    Strongly typed with WebSearchResultItem elements.
    Contains strictly zero citation IDs or execution identifiers.
    """
    query: str = Field(..., description="Safe, privacy-transformed search query.")
    results: List[WebSearchResultItem] = Field(default_factory=list, description="Validated search result items.")
    total_results: int = Field(0, description="Total number of results returned.")
    cached: bool = Field(False, description="True if satisfied from the user-scoped in-memory cache.")
