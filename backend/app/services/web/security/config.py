"""
Server-controlled network security constants and configuration for Web Retrieval.
These settings are strictly authoritative and cannot be overridden by LLM tool arguments.
"""
from dataclasses import dataclass
from typing import Tuple


DEFAULT_CONNECT_TIMEOUT_SECONDS: float = 2.0
DEFAULT_READ_TIMEOUT_SECONDS: float = 3.0
DEFAULT_TOTAL_TIMEOUT_SECONDS: float = 5.0
DEFAULT_MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024  # 2 MB stream cap
DEFAULT_MAX_REDIRECTS: int = 3
DEFAULT_MAX_TEXT_EXTRACT_CHARS: int = 12_000
ALLOWED_SCHEMES: Tuple[str, ...] = ("http", "https")
ALLOWED_PORTS: Tuple[int, ...] = (80, 443)


@dataclass(frozen=True)
class WebSecurityConfig:
    """
    Immutable server-side configuration for web transport and security limits.
    """
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    total_timeout_seconds: float = DEFAULT_TOTAL_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    max_text_extract_chars: int = DEFAULT_MAX_TEXT_EXTRACT_CHARS
    allowed_schemes: Tuple[str, ...] = ALLOWED_SCHEMES
    allowed_ports: Tuple[int, ...] = ALLOWED_PORTS
