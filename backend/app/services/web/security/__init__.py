"""
Security subpackage for web retrieval.
Exports URL validation, SSRF defense, DNS resolution, pinned transport, and redirect controls.
"""
from app.services.web.security.config import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_MAX_TEXT_EXTRACT_CHARS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_TOTAL_TIMEOUT_SECONDS,
    WebSecurityConfig,
)
from app.services.web.security.dns import (
    BaseDNSResolver,
    SystemDNSResolver,
    get_validated_destination_ips,
    resolve_and_validate_hostname,
)
from app.services.web.security.exceptions import (
    DNSResolutionError,
    HTTPSDowngradeBlockedError,
    MaxRedirectsExceededError,
    RequestTimeoutError,
    ResponseSizeLimitExceededError,
    SSRFBlockedError,
    TLSSecurityError,
    URLValidationError,
    WebSecurityError,
)
from app.services.web.security.redirect import validate_redirect
from app.services.web.security.ssrf import (
    BLOCKED_IPV4_NETWORKS,
    BLOCKED_IPV6_NETWORKS,
    is_ip_allowed,
    validate_ip_address,
)
from app.services.web.security.transport import (
    PinnedAsyncHTTPTransport,
    PinnedNetworkBackend,
    SecureWebResponse,
    execute_secure_fetch,
)
from app.services.web.security.url import (
    ValidatedURL,
    normalize_and_validate_url,
    parse_ip_literal,
)

__all__ = [
    # Config
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_TOTAL_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_MAX_TEXT_EXTRACT_CHARS",
    "WebSecurityConfig",
    # Exceptions
    "WebSecurityError",
    "URLValidationError",
    "SSRFBlockedError",
    "DNSResolutionError",
    "HTTPSDowngradeBlockedError",
    "MaxRedirectsExceededError",
    "TLSSecurityError",
    "ResponseSizeLimitExceededError",
    "RequestTimeoutError",
    # URL
    "ValidatedURL",
    "normalize_and_validate_url",
    "parse_ip_literal",
    # SSRF
    "BLOCKED_IPV4_NETWORKS",
    "BLOCKED_IPV6_NETWORKS",
    "is_ip_allowed",
    "validate_ip_address",
    # DNS
    "BaseDNSResolver",
    "SystemDNSResolver",
    "resolve_and_validate_hostname",
    "get_validated_destination_ips",
    # Redirect
    "validate_redirect",
    # Transport
    "PinnedNetworkBackend",
    "PinnedAsyncHTTPTransport",
    "SecureWebResponse",
    "execute_secure_fetch",
]
