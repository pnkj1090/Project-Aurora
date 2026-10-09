"""
Redirect security and policy validation module.
Enforces redirect budgets, HTTPS downgrade protection, and full pipeline restart on each hop.
"""
from typing import Optional
import urllib.parse

from app.services.web.security.config import DEFAULT_MAX_REDIRECTS, WebSecurityConfig
from app.services.web.security.exceptions import (
    HTTPSDowngradeBlockedError,
    MaxRedirectsExceededError,
)
from app.services.web.security.url import ValidatedURL, normalize_and_validate_url


def validate_redirect(
    current_url: ValidatedURL,
    redirect_location: str,
    current_redirect_count: int,
    config: Optional[WebSecurityConfig] = None,
) -> ValidatedURL:
    """
    Validates a redirect target against the redirect budget, downgrade policy,
    and restarts full URL normalization and validation.

    Rules:
    - If current_redirect_count >= max_redirects (default 3), raises MaxRedirectsExceededError.
    - Resolves relative URLs against current_url.canonical_url.
    - If current URL is HTTPS and target is HTTP, raises HTTPSDowngradeBlockedError.
    - Re-runs complete normalize_and_validate_url on the target.
    - Never silently strips fragments or bypasses port/host checks.
    """
    max_redirects = config.max_redirects if config else DEFAULT_MAX_REDIRECTS

    if current_redirect_count >= max_redirects:
        raise MaxRedirectsExceededError(
            f"Maximum redirect limit ({max_redirects}) exceeded. Aborting redirect chain."
        )

    if not isinstance(redirect_location, str) or not redirect_location.strip():
        raise ValueError("Redirect location must be a non-empty string.")

    # Resolve relative URL against current canonical URL
    resolved_target = urllib.parse.urljoin(current_url.canonical_url, redirect_location.strip())

    # Re-enter the complete URL normalization and security pipeline
    target_validated_url = normalize_and_validate_url(resolved_target, config=config)

    # Enforce HTTPS downgrade protection
    if current_url.scheme == "https" and target_validated_url.scheme == "http":
        raise HTTPSDowngradeBlockedError(
            f"Insecure protocol downgrade from HTTPS ({current_url.canonical_url}) "
            f"to HTTP ({target_validated_url.canonical_url}) is strictly prohibited."
        )

    return target_validated_url
