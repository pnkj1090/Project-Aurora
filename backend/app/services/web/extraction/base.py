"""
Base abstraction for web extraction engines.
Decouples parsing implementation so Trafilatura, BeautifulSoup, or built-in parsers
can be interchanged without affecting reader services or client layers.
"""
from abc import ABC, abstractmethod

from app.services.web.extraction.models import ExtractedWebContent
from app.services.web.security.config import DEFAULT_MAX_TEXT_EXTRACT_CHARS


class BaseExtractionEngine(ABC):
    """
    Abstract interface for safe HTML text and metadata extraction.
    """
    @abstractmethod
    def extract_text_and_metadata(
        self,
        html_bytes: bytes,
        base_url: str,
        max_chars: int = DEFAULT_MAX_TEXT_EXTRACT_CHARS,
    ) -> ExtractedWebContent:
        """
        Extracts clean human-readable text, title, and metadata from raw HTML bytes.
        
        Guarantees:
        - Active scripts, styles, iframes, and executable objects are stripped.
        - Output text length is capped at max_chars.
        - Content is treated strictly as passive data.
        """
        pass
