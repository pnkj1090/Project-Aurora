"""
Web search provider package.
"""
from app.services.web.providers.base import BaseWebSearchProvider
from app.services.web.providers.mock import MockWebSearchProvider
from app.services.web.providers.models import WebSearchResponse, WebSearchResultItem

__all__ = [
    "BaseWebSearchProvider",
    "MockWebSearchProvider",
    "WebSearchResultItem",
    "WebSearchResponse",
]
