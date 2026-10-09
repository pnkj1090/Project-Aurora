"""
Safe web reader package.
"""
from app.services.web.reader.models import WebPageReadResult, WebSearchToolResult
from app.services.web.reader.service import SafeWebReaderService

__all__ = [
    "SafeWebReaderService",
    "WebPageReadResult",
    "WebSearchToolResult",
]
