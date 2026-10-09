"""
Typed exceptions for web security, SSRF validation, DNS resolution, and pinned transport.
All errors fail closed and provide clean security diagnostics.
"""


class WebSecurityError(Exception):
    """Base exception for all web security failures."""
    pass


class URLValidationError(WebSecurityError):
    """Raised when a URL violates structural, scheme, port, fragment, or hostname rules."""
    pass


# Alias for compatibility
InvalidURLError = URLValidationError


class SSRFBlockedError(WebSecurityError):
    """Raised when an IP address violates SSRF restrictions (private, loopback, reserved, etc.)."""
    pass


class DNSResolutionError(WebSecurityError):
    """Raised when DNS resolution fails, times out, or returns no usable records."""
    pass


class HTTPSDowngradeBlockedError(WebSecurityError):
    """Raised when a redirect attempts to downgrade from HTTPS to HTTP."""
    pass


class MaxRedirectsExceededError(WebSecurityError):
    """Raised when redirect chain exceeds the maximum allowed hops (default 3)."""
    pass


class TLSSecurityError(WebSecurityError):
    """Raised when TLS certificate verification or hostname matching fails."""
    pass


class ResponseSizeLimitExceededError(WebSecurityError):
    """Raised when the raw HTTP response stream exceeds the maximum allowed bytes (default 2 MB)."""
    pass


class RequestTimeoutError(WebSecurityError):
    """Raised when connection or read timeout expires."""
    pass
