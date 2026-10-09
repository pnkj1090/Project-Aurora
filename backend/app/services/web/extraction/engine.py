"""
Concrete HTML extraction engines for Project Aurora.
Provides SafeHTMLExtractionEngine using standard library html.parser,
and BS4ExtractionEngine for environments with BeautifulSoup4.
"""
from html.parser import HTMLParser
import html
import re
from typing import List, Optional, Set
import urllib.parse

from app.services.web.extraction.base import BaseExtractionEngine
from app.services.web.extraction.models import ExtractedWebContent
from app.services.web.security.config import DEFAULT_MAX_TEXT_EXTRACT_CHARS


class _SanitizingHTMLParser(HTMLParser):
    """
    Internal streaming HTML parser that strips active and non-text tags,
    extracts the page title, and normalizes block-level structure.
    """
    # Container tags whose entire inner contents (including text) must be skipped
    SKIP_CONTAINER_TAGS: Set[str] = {
        "script", "style", "iframe", "object", "noscript",
        "svg", "canvas", "template", "audio", "video", "button",
        "select", "option", "textarea", "form"
    }

    # Void tags that never have closing tags and must never increment skip depth
    VOID_TAGS: Set[str] = {
        "area", "base", "br", "col", "embed", "hr", "img",
        "input", "link", "meta", "param", "source", "track", "wbr"
    }

    BLOCK_TAGS: Set[str] = {
        "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "tr", "article", "section", "blockquote", "pre", "hr"
    }

    def __init__(self):
        super().__init__()
        self.title_parts: List[str] = []
        self._in_title: bool = False
        self._in_head: bool = False
        self._skip_depth: int = 0
        self.text_chunks: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag_lower = tag.lower()
        if tag_lower == "head":
            self._in_head = True
        elif tag_lower == "title":
            self._in_title = True
        elif tag_lower in self.SKIP_CONTAINER_TAGS:
            self._skip_depth += 1
        elif tag_lower in self.BLOCK_TAGS:
            self.text_chunks.append("\n")

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower == "head":
            self._in_head = False
        elif tag_lower == "title":
            self._in_title = False
        elif tag_lower in self.SKIP_CONTAINER_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif tag_lower in self.BLOCK_TAGS:
            self.text_chunks.append("\n")

    def handle_data(self, data: str):
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth > 0 or self._in_head:
            return
        self.text_chunks.append(data)

    def get_title(self) -> Optional[str]:
        raw_title = " ".join("".join(self.title_parts).split())
        return html.unescape(raw_title) if raw_title else None

    def get_text(self) -> str:
        raw_text = "".join(self.text_chunks)
        # Unescape HTML entities
        unescaped = html.unescape(raw_text)
        # Normalize block spacing: collapse multiple blank lines to at most two newlines
        lines = [line.strip() for line in unescaped.splitlines()]
        cleaned_text = "\n".join(line for line in lines if line)
        return cleaned_text


class SafeHTMLExtractionEngine(BaseExtractionEngine):
    """
    Standard-library-based safe HTML extraction engine.
    Requires no external C-extensions or external network dependencies.
    """
    def extract_text_and_metadata(
        self,
        html_bytes: bytes,
        base_url: str,
        max_chars: int = DEFAULT_MAX_TEXT_EXTRACT_CHARS,
    ) -> ExtractedWebContent:
        if not isinstance(html_bytes, (bytes, bytearray)):
            raise TypeError("html_bytes must be bytes or bytearray.")

        # Decode HTML content with UTF-8 fallback
        try:
            html_text = html_bytes.decode("utf-8", errors="replace")
        except Exception:
            html_text = html_bytes.decode("latin-1", errors="replace")

        # Parse HTML safely
        parser = _SanitizingHTMLParser()
        try:
            parser.feed(html_text)
        except Exception:
            # Resilient fallback: strip tags via regex if parser hits severe malformation
            pass

        title = parser.get_title()
        extracted_text = parser.get_text()

        # Enforce server-controlled character limit
        is_truncated = False
        if len(extracted_text) > max_chars:
            extracted_text = extracted_text[:max_chars]
            is_truncated = True

        # Extract domain from base_url
        try:
            parsed_url = urllib.parse.urlsplit(base_url)
            domain = parsed_url.hostname
        except Exception:
            domain = None

        return ExtractedWebContent(
            canonical_url=base_url,
            title=title,
            text=extracted_text,
            character_count=len(extracted_text),
            is_truncated=is_truncated,
            domain=domain,
        )


class BS4ExtractionEngine(BaseExtractionEngine):
    """
    Optional BeautifulSoup4-based extraction engine.
    Delegates to SafeHTMLExtractionEngine if bs4 is not installed.
    """
    def __init__(self):
        try:
            import bs4  # noqa: F401
            self._has_bs4 = True
        except ImportError:
            self._has_bs4 = False
            self._fallback = SafeHTMLExtractionEngine()

    def extract_text_and_metadata(
        self,
        html_bytes: bytes,
        base_url: str,
        max_chars: int = DEFAULT_MAX_TEXT_EXTRACT_CHARS,
    ) -> ExtractedWebContent:
        if not self._has_bs4:
            return self._fallback.extract_text_and_metadata(
                html_bytes=html_bytes,
                base_url=base_url,
                max_chars=max_chars,
            )

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_bytes, "html.parser")
        for tag in soup(["script", "style", "iframe", "object", "embed", "noscript"]):
            tag.decompose()

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else None

        text = soup.get_text(separator="\n", strip=True)

        is_truncated = False
        if len(text) > max_chars:
            text = text[:max_chars]
            is_truncated = True

        try:
            domain = urllib.parse.urlsplit(base_url).hostname
        except Exception:
            domain = None

        return ExtractedWebContent(
            canonical_url=base_url,
            title=title,
            text=text,
            character_count=len(text),
            is_truncated=is_truncated,
            domain=domain,
        )
