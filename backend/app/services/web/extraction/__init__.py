"""
Web content extraction package for Project Aurora.
Provides pluggable HTML extraction engines that treat web content strictly as untrusted data.
"""
from app.services.web.extraction.base import BaseExtractionEngine
from app.services.web.extraction.engine import BS4ExtractionEngine, SafeHTMLExtractionEngine
from app.services.web.extraction.models import ExtractedWebContent

# Default engine is SafeHTMLExtractionEngine
DefaultExtractionEngine = SafeHTMLExtractionEngine

__all__ = [
    "BaseExtractionEngine",
    "SafeHTMLExtractionEngine",
    "BS4ExtractionEngine",
    "DefaultExtractionEngine",
    "ExtractedWebContent",
]
