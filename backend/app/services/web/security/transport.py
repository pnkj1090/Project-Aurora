"""
Pinned socket transport and secure fetch module.
Directly connects to pre-validated IP addresses with full TLS verification and stream caps.
Bypasses secondary DNS resolution to guarantee protection against DNS rebinding / TOCTOU.
"""
import asyncio
from dataclasses import dataclass
import ipaddress
import ssl
from typing import Dict, List, Optional, Union

import httpcore
import httpcore._backends.anyio
import httpx

from app.services.web.security.config import DEFAULT_TOTAL_TIMEOUT_SECONDS, WebSecurityConfig
from app.services.web.security.dns import BaseDNSResolver, get_validated_destination_ips
from app.services.web.security.exceptions import (
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
from app.services.web.security.ssrf import validate_ip_address
from app.services.web.security.url import ValidatedURL


@dataclass(frozen=True)
class SecureWebResponse:
    """
    Result of a securely executed web fetch.
    """
    status_code: int
    headers: Dict[str, str]
    content: bytes
    canonical_url: str
    final_ip: str
    redirect_history: List[str]


class PinnedNetworkBackend(httpcore._backends.anyio.AnyIOBackend):
    """
    Custom httpcore network backend that intercepts socket connection calls
    and routes them directly to a pre-validated IP address without performing DNS lookups.
    """
    def __init__(self, pinned_ip: str):
        super().__init__()
        self._pinned_ip = pinned_ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        **kwargs,
    ) -> httpcore.AsyncNetworkStream:
        # Connect directly to the pre-validated pinned IP, bypassing any secondary hostname resolution
        return await super().connect_tcp(host=self._pinned_ip, port=port, **kwargs)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """
    HTTPX asynchronous transport pinned directly to a validated IP address.
    Strictly enforces TLS certificate verification and SNI with the canonical hostname.
    """
    def __init__(
        self,
        pinned_ip: str,
        verify: bool = True,
        ssl_context: Optional[ssl.SSLContext] = None,
        **kwargs,
    ):
        if not verify:
            raise TLSSecurityError("Disabling TLS verification is strictly prohibited.")
        if ssl_context is not None:
            if ssl_context.verify_mode == ssl.CERT_NONE:
                raise TLSSecurityError("Disabling TLS certificate verification is strictly prohibited.")
            if not ssl_context.check_hostname:
                raise TLSSecurityError("Disabling TLS hostname verification is strictly prohibited.")

        self.pinned_ip = pinned_ip
        network_backend = PinnedNetworkBackend(pinned_ip)

        if ssl_context is None:
            ssl_context = httpx.create_ssl_context(verify=True)

        super().__init__(verify=ssl_context, **kwargs)

        # Replace standard pool with one bound to the PinnedNetworkBackend
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            network_backend=network_backend,
            http1=True,
            http2=False,
            retries=0,
        )


async def execute_secure_fetch(
    validated_url: ValidatedURL,
    destination_ip: Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]] = None,
    resolver: Optional[BaseDNSResolver] = None,
    config: Optional[WebSecurityConfig] = None,
    ssl_context: Optional[ssl.SSLContext] = None,
) -> SecureWebResponse:
    """
    Executes a secure, single-turn or redirected GET request using pinned socket transport.

    Enforces:
    - Pre-resolution and all-or-nothing SSRF check for every hop.
    - Pinned direct socket connection to the validated destination IP.
    - Full TLS verification against the canonical hostname (no cert bypass).
    - HTTPS -> HTTP downgrade prohibition.
    - Maximum 3 redirects.
    - 2 MB maximum response stream cap.
    - Strict server-controlled timeout budget.
    """
    cfg = config or WebSecurityConfig()

    try:
        return await asyncio.wait_for(
            _fetch_loop(
                validated_url=validated_url,
                destination_ip=destination_ip,
                resolver=resolver,
                cfg=cfg,
                ssl_context=ssl_context,
            ),
            timeout=cfg.total_timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise RequestTimeoutError(
            f"Web fetch for '{validated_url.canonical_url}' timed out after {cfg.total_timeout_seconds}s total budget."
        ) from exc


async def _fetch_loop(
    validated_url: ValidatedURL,
    destination_ip: Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]],
    resolver: Optional[BaseDNSResolver],
    cfg: WebSecurityConfig,
    ssl_context: Optional[ssl.SSLContext],
) -> SecureWebResponse:
    current_url = validated_url
    current_ip = destination_ip
    redirect_count = 0
    redirect_history: List[str] = []

    while True:
        # Resolve & validate destination IP if not already validated
        if current_ip is None:
            resolved_ips = await get_validated_destination_ips(
                current_url,
                resolver=resolver,
                timeout_seconds=cfg.connect_timeout_seconds,
            )
            current_ip = resolved_ips[0]
        else:
            validate_ip_address(current_ip)

        pinned_ip_str = str(current_ip)

        # Build pinned transport
        transport = PinnedAsyncHTTPTransport(
            pinned_ip=pinned_ip_str,
            ssl_context=ssl_context,
        )

        timeout = httpx.Timeout(
            connect=cfg.connect_timeout_seconds,
            read=cfg.read_timeout_seconds,
            write=cfg.connect_timeout_seconds,
            pool=cfg.connect_timeout_seconds,
        )

        client_headers = {
            "User-Agent": "Aurora/1.0 (Information Grounding; +https://project-aurora.local)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        }

        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                async with client.stream("GET", current_url.canonical_url, headers=client_headers) as response:
                    # Check for redirect status
                    if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                        redirect_location = response.headers["location"]
                        redirect_history.append(current_url.canonical_url)

                        # Validate redirect against security policy (downgrade, budget, URL syntax)
                        next_url = validate_redirect(
                            current_url=current_url,
                            redirect_location=redirect_location,
                            current_redirect_count=redirect_count,
                            config=cfg,
                        )

                        current_url = next_url
                        current_ip = None  # Force fresh DNS & SSRF validation for next hop
                        redirect_count += 1
                        continue

                    # Stream and enforce maximum byte limit
                    chunks: List[bytes] = []
                    total_bytes = 0

                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > cfg.max_response_bytes:
                            raise ResponseSizeLimitExceededError(
                                f"Response stream exceeded maximum byte limit of {cfg.max_response_bytes} bytes."
                            )
                        chunks.append(chunk)

                    raw_content = b"".join(chunks)

                    return SecureWebResponse(
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        content=raw_content,
                        canonical_url=current_url.canonical_url,
                        final_ip=pinned_ip_str,
                        redirect_history=redirect_history,
                    )

        except (
            SSRFBlockedError,
            URLValidationError,
            HTTPSDowngradeBlockedError,
            MaxRedirectsExceededError,
            ResponseSizeLimitExceededError,
        ):
            raise
        except (httpx.ConnectTimeout, httpx.ReadTimeout, TimeoutError) as exc:
            raise RequestTimeoutError(f"HTTP request timed out: {exc}") from exc
        except (ssl.SSLCertVerificationError, ssl.SSLError) as exc:
            raise TLSSecurityError(f"TLS security validation failed: {exc}") from exc
        except httpx.ConnectError as exc:
            # Check if inner cause was SSL failure
            cause = getattr(exc, "__cause__", None)
            if isinstance(cause, ssl.SSLError):
                raise TLSSecurityError(f"TLS connection failed: {cause}") from exc
            raise WebSecurityError(f"Failed to connect to {current_url.canonical_url} at {pinned_ip_str}: {exc}") from exc
        except Exception as exc:
            raise WebSecurityError(f"Unexpected transport failure: {exc}") from exc
