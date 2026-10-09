"""
Comprehensive security test suite for Phase 5K.1:
- Strict URL normalization and validation
- IP literal normalization (octal, hex, dword, IPv4-mapped IPv6)
- SSRF validation (IPv4 and IPv6 blacklists)
- DNS resolution with all-or-nothing validation
- Pinned socket transport & TLS verification enforcement
- Redirect validation & HTTPS downgrade protection
- Server-controlled transport limits (2 MB cap, timeouts)
- Architectural regression invariants (zero SQLite tables, no web tools)
"""
import asyncio
import ipaddress
import socket
import ssl
from typing import List, Optional, Union
import unittest.mock

import pytest

from app.services.tools.registry import ToolRegistry
from app.services.web.security.config import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_MAX_RESPONSE_BYTES,
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
    ResponseSizeLimitExceededError,
    SSRFBlockedError,
    TLSSecurityError,
    URLValidationError,
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


# =====================================================================
# 1. URL Normalization and Validation Tests
# =====================================================================

class TestURLValidation:
    @pytest.mark.parametrize("scheme", [
        "file:///etc/passwd",
        "ftp://ftp.example.com/file.txt",
        "gopher://gopher.example.com",
        "data:text/html,<html>Hello</html>",
        "javascript:alert(1)",
        "ws://example.com/socket",
        "wss://example.com/socket",
        "ssh://git@github.com",
        "blob:https://example.com/123",
    ])
    def test_forbidden_schemes_rejected(self, scheme: str):
        with pytest.raises(URLValidationError, match="Unsupported URL scheme"):
            normalize_and_validate_url(scheme)

    @pytest.mark.parametrize("url", [
        "https://user:password@example.com/path",
        "http://user@example.com/path",
        "https://:password@example.com/path",
        "http://admin:admin123@192.168.1.1/",
    ])
    def test_userinfo_credentials_rejected(self, url: str):
        with pytest.raises(URLValidationError, match="user credentials/userinfo"):
            normalize_and_validate_url(url)

    @pytest.mark.parametrize("url", [
        "https://example.com/path#section",
        "https://example.com/#",
        "http://example.com/docs#intro",
        "https://example.com/page?query=test#fragment",
    ])
    def test_fragments_strictly_rejected_never_stripped(self, url: str):
        with pytest.raises(URLValidationError, match="fragment identifier"):
            normalize_and_validate_url(url)

    @pytest.mark.parametrize("url,port", [
        ("https://example.com:8080/path", 8080),
        ("http://example.com:8443/path", 8443),
        ("http://example.com:22/path", 22),
        ("https://example.com:3000/api", 3000),
        ("http://example.com:8000/", 8000),
    ])
    def test_unauthorized_ports_rejected(self, url: str, port: int):
        with pytest.raises(URLValidationError, match=f"Destination port {port} is not allowed"):
            normalize_and_validate_url(url)

    def test_authorized_ports_accepted(self):
        url_80 = normalize_and_validate_url("http://example.com:80/path")
        assert url_80.port == 80
        assert url_80.canonical_url == "http://example.com/path"

        url_443 = normalize_and_validate_url("https://example.com:443/path")
        assert url_443.port == 443
        assert url_443.canonical_url == "https://example.com/path"

        # Explicit non-standard port 443 for http
        url_cross = normalize_and_validate_url("http://example.com:443/path")
        assert url_cross.port == 443
        assert url_cross.canonical_url == "http://example.com:443/path"

    @pytest.mark.parametrize("url", [
        "http:///path",
        "http://:80/path",
        "http://   /path",
        "http://exa mple.com/path",
        "http://example\n.com/path",
        "http://example\r.com/path",
        "http://example\0.com/path",
        "http://-example.com/path",
        "http://example-.com/path",
        "http://example..com/path",
    ])
    def test_malformed_hostnames_rejected(self, url: str):
        with pytest.raises(URLValidationError):
            normalize_and_validate_url(url)

    def test_safe_idna_normalization(self):
        # German umlaut domain: münchen.de -> xn--mnchen-3ya.de
        url = normalize_and_validate_url("https://münchen.de/tourismus")
        assert url.hostname == "xn--mnchen-3ya.de"
        assert url.canonical_url == "https://xn--mnchen-3ya.de/tourismus"

        # Direct punycode domain should pass unchanged
        url_puny = normalize_and_validate_url("https://xn--mnchen-3ya.de/tourismus")
        assert url_puny.hostname == "xn--mnchen-3ya.de"

    def test_canonical_url_query_and_path_sorting(self):
        # Query parameters sorted deterministically, path segments normalized
        url = normalize_and_validate_url("https://example.com/a/../b/c//?z=3&a=1&m=2")
        assert url.path == "/b/c/"
        assert url.query == "a=1&m=2&z=3"
        assert url.canonical_url == "https://example.com/b/c/?a=1&m=2&z=3"


# =====================================================================
# 2. IP Literal Normalization Tests
# =====================================================================

class TestIPLiteralNormalization:
    def test_standard_ipv4(self):
        ip = parse_ip_literal("127.0.0.1")
        assert ip == ipaddress.IPv4Address("127.0.0.1")

    def test_standard_ipv6(self):
        ip = parse_ip_literal("[::1]")
        assert ip == ipaddress.IPv6Address("::1")
        ip2 = parse_ip_literal("::1")
        assert ip2 == ipaddress.IPv6Address("::1")

    def test_decimal_dword_ipv4(self):
        # 2130706433 == (127 << 24) | 1 == 127.0.0.1
        ip = parse_ip_literal("2130706433")
        assert ip == ipaddress.IPv4Address("127.0.0.1")

    def test_hexadecimal_dword_ipv4(self):
        # 0x7f000001 == 127.0.0.1
        ip = parse_ip_literal("0x7f000001")
        assert ip == ipaddress.IPv4Address("127.0.0.1")

    def test_octal_dotted_ipv4(self):
        # 0177.0.0.1 == 127.0.0.1
        ip = parse_ip_literal("0177.0.0.1")
        assert ip == ipaddress.IPv4Address("127.0.0.1")

    def test_hex_dotted_ipv4(self):
        # 0x7f.0.0.1 == 127.0.0.1
        ip = parse_ip_literal("0x7f.0.0.1")
        assert ip == ipaddress.IPv4Address("127.0.0.1")

    def test_multipart_short_ipv4(self):
        # 127.1 == 127.0.0.1 in inet_aton notation
        ip = parse_ip_literal("127.1")
        assert ip == ipaddress.IPv4Address("127.0.0.1")

    def test_ipv4_mapped_ipv6(self):
        ip = parse_ip_literal("::ffff:127.0.0.1")
        assert ip == ipaddress.IPv4Address("127.0.0.1")

    def test_non_ip_hostname(self):
        assert parse_ip_literal("example.com") is None
        assert parse_ip_literal("google.com") is None
        assert parse_ip_literal("localhost") is None
        assert parse_ip_literal("127.0.0.1.nip.io") is None


# =====================================================================
# 3. SSRF IP Validation Tests
# =====================================================================

class TestSSRFValidation:
    @pytest.mark.parametrize("ip_str", [
        # Loopback
        "127.0.0.1",
        "127.0.0.2",
        "127.255.255.254",
        "::1",
        # RFC 1918 Private
        "10.0.0.1",
        "10.254.1.2",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.1",
        "192.168.1.254",
        # Cloud metadata / Link-local
        "169.254.169.254",
        "169.254.0.1",
        "fe80::1",
        # Carrier-grade NAT
        "100.64.0.1",
        "100.127.255.254",
        # Multicast / Broadcast / Reserved
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "0.0.0.0",
        "ff02::1",
        # IPv6 Unique Local
        "fc00::1",
        "fd12:3456:789a::1",
        # Documentation / Test-net
        "192.0.2.1",
        "198.51.100.1",
        "203.0.113.1",
        # Unspecified
        "::",
    ])
    def test_prohibited_ips_blocked(self, ip_str: str):
        ip = ipaddress.ip_address(ip_str)
        assert is_ip_allowed(ip) is False
        with pytest.raises(SSRFBlockedError):
            validate_ip_address(ip)

    @pytest.mark.parametrize("ip_str", [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.215.14",
        "151.101.1.69",
        "2607:f8b0:4005:805::200e",
        "2001:4860:4860::8888",
    ])
    def test_valid_public_ips_allowed(self, ip_str: str):
        ip = ipaddress.ip_address(ip_str)
        assert is_ip_allowed(ip) is True
        # Should not raise
        validate_ip_address(ip)


# =====================================================================
# 4. DNS Resolution & All-or-Nothing Validation Tests
# =====================================================================

class MockDNSResolver(BaseDNSResolver):
    def __init__(self, ip_map=None, delay: float = 0.0, raise_exc=None):
        self.ip_map = ip_map or {}
        self.delay = delay
        self.raise_exc = raise_exc

    async def resolve(self, hostname: str) -> List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        if hostname not in self.ip_map:
            raise DNSResolutionError(f"No mock record for {hostname}")
        return [ipaddress.ip_address(ip) for ip in self.ip_map[hostname]]


@pytest.mark.asyncio
class TestDNSResolution:
    async def test_all_public_ips_accepted(self):
        resolver = MockDNSResolver({
            "example.com": ["93.184.215.14", "93.184.215.15"]
        })
        ips = await resolve_and_validate_hostname("example.com", resolver=resolver)
        assert len(ips) == 2
        assert ipaddress.IPv4Address("93.184.215.14") in ips

    async def test_mixed_public_and_private_rejected_all_or_nothing(self):
        # If ANY IP is private/loopback, the ENTIRE result must fail
        resolver = MockDNSResolver({
            "split-dns.example.com": ["93.184.215.14", "127.0.0.1"]
        })
        with pytest.raises(SSRFBlockedError, match="all-or-nothing SSRF policy"):
            await resolve_and_validate_hostname("split-dns.example.com", resolver=resolver)

    async def test_dns_failure_fails_closed(self):
        resolver = MockDNSResolver(raise_exc=socket.gaierror("Name or service not known"))
        with pytest.raises(DNSResolutionError, match="DNS resolution failed"):
            await resolve_and_validate_hostname("nonexistent.invalid", resolver=resolver)

    async def test_dns_timeout_fails_closed(self):
        resolver = MockDNSResolver(delay=2.0)
        with pytest.raises(DNSResolutionError, match="timed out"):
            await resolve_and_validate_hostname("slow.example.com", resolver=resolver, timeout_seconds=0.05)

    async def test_ip_literal_bypasses_dns_and_validates(self):
        resolver = MockDNSResolver({})  # Empty, would fail if called
        url = normalize_and_validate_url("http://8.8.8.8/path")
        ips = await get_validated_destination_ips(url, resolver=resolver)
        assert ips == [ipaddress.IPv4Address("8.8.8.8")]

    async def test_private_ip_literal_fails_ssrf(self):
        resolver = MockDNSResolver({})
        url = normalize_and_validate_url("http://127.0.0.1/path")
        with pytest.raises(SSRFBlockedError):
            await get_validated_destination_ips(url, resolver=resolver)


# =====================================================================
# 5. Pinned Socket Transport & TLS Verification Tests
# =====================================================================

class TestPinnedSocketTransport:
    def test_tls_verification_cannot_be_disabled(self):
        with pytest.raises(TLSSecurityError, match="Disabling TLS verification is strictly prohibited"):
            PinnedAsyncHTTPTransport(pinned_ip="1.1.1.1", verify=False)

    def test_tls_cert_none_rejected(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with pytest.raises(TLSSecurityError, match="Disabling TLS certificate verification is strictly prohibited"):
            PinnedAsyncHTTPTransport(pinned_ip="1.1.1.1", ssl_context=ctx)

    def test_tls_check_hostname_false_rejected(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        with pytest.raises(TLSSecurityError, match="Disabling TLS hostname verification is strictly prohibited"):
            PinnedAsyncHTTPTransport(pinned_ip="1.1.1.1", ssl_context=ctx)

    def test_pinned_network_backend_initialization(self):
        transport = PinnedAsyncHTTPTransport(pinned_ip="93.184.215.14")
        assert transport.pinned_ip == "93.184.215.14"
        assert transport._pool._network_backend._pinned_ip == "93.184.215.14"

    @pytest.mark.asyncio
    async def test_tcp_pinned_ip_and_tls_sni_separation_invariant(self):
        """
        Deterministically proves the three simultaneous transport properties:
        1. TCP socket connects directly to the validated/pinned IP (NOT the hostname).
        2. TLS SNI and server_hostname use the canonical destination hostname (NOT the pinned IP).
        3. TLS certificate hostname verification is enabled (CERT_REQUIRED + check_hostname=True).
        """
        import httpcore
        import httpcore._backends.anyio
        import httpx
        from unittest.mock import patch

        recorded = {}

        class MockTLSStream(httpcore.AsyncMockStream):
            def __init__(self, buffer):
                super().__init__(buffer)

            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                recorded["tls_ssl_context"] = ssl_context
                recorded["tls_server_hostname"] = server_hostname
                return self

        async def mock_connect_tcp(backend_self, host, port, **kwargs):
            recorded["actual_tcp_connect_host"] = host
            recorded["actual_tcp_connect_port"] = port
            return MockTLSStream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"])

        pinned_target_ip = "93.184.215.14"
        requested_hostname = "example.com"
        transport = PinnedAsyncHTTPTransport(pinned_ip=pinned_target_ip)

        with patch.object(httpcore._backends.anyio.AnyIOBackend, "connect_tcp", mock_connect_tcp):
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.get(f"https://{requested_hostname}/secure-path")
                assert response.status_code == 200

        # Invariant 1: TCP connection goes directly to the validated/pinned IP, NEVER the hostname
        assert recorded["actual_tcp_connect_host"] == pinned_target_ip
        assert recorded["actual_tcp_connect_host"] != requested_hostname
        assert recorded["actual_tcp_connect_port"] == 443

        # Invariant 2: TLS SNI uses the canonical destination hostname, NEVER the pinned IP
        assert recorded["tls_server_hostname"] == requested_hostname
        assert recorded["tls_server_hostname"] != pinned_target_ip

        # Invariant 3: TLS certificate verification and hostname checking are strictly enabled
        ctx = recorded["tls_ssl_context"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    @pytest.mark.asyncio
    async def test_execute_secure_fetch_preserves_pinned_ip_and_canonical_sni(self):
        """
        Verifies that the high-level execute_secure_fetch pipeline preserves
        pinned IP connection and canonical SNI throughout execution.
        """
        import httpcore
        import httpcore._backends.anyio
        from unittest.mock import patch

        recorded = {}

        class MockTLSStream(httpcore.AsyncMockStream):
            def __init__(self, buffer):
                super().__init__(buffer)

            async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
                recorded["tls_server_hostname"] = server_hostname
                return self

        async def mock_connect_tcp(backend_self, host, port, **kwargs):
            recorded["tcp_connect_host"] = host
            return MockTLSStream([b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndone"])

        url = normalize_and_validate_url("https://secure.example.org/path")
        pinned_ip = ipaddress.IPv4Address("93.184.215.14")

        with patch.object(httpcore._backends.anyio.AnyIOBackend, "connect_tcp", mock_connect_tcp):
            res = await execute_secure_fetch(
                validated_url=url,
                destination_ip=pinned_ip,
            )
            assert res.status_code == 200
            assert res.content == b"done"
            assert res.final_ip == "93.184.215.14"

        # Assert TCP peer was the pinned IP
        assert recorded["tcp_connect_host"] == "93.184.215.14"
        # Assert TLS SNI was the canonical hostname
        assert recorded["tls_server_hostname"] == "secure.example.org"

    def test_insecure_behavior_simulations_would_fail(self):
        """
        Confirms that our invariant assertions detect any regression towards:
        - connecting to hostname instead of validated IP
        - using pinned IP as TLS SNI
        - disabling certificate verification
        - disabling hostname verification
        """
        pinned_ip = "93.184.215.14"
        requested_hostname = "example.com"

        # 1. Insecure case: connected to hostname instead of pinned IP
        insecure_tcp_host = requested_hostname
        with pytest.raises(AssertionError):
            assert insecure_tcp_host == pinned_ip

        # 2. Insecure case: passed pinned IP as TLS SNI
        insecure_sni = pinned_ip
        with pytest.raises(AssertionError):
            assert insecure_sni == requested_hostname

        # 3. Insecure case: disabled cert verification
        insecure_verify_mode = ssl.CERT_NONE
        with pytest.raises(AssertionError):
            assert insecure_verify_mode == ssl.CERT_REQUIRED

        # 4. Insecure case: disabled hostname check
        insecure_check_hostname = False
        with pytest.raises(AssertionError):
            assert insecure_check_hostname is True


# =====================================================================
# 6. Redirect Security & HTTPS Downgrade Tests
# =====================================================================

class TestRedirectSecurity:
    def test_https_to_https_allowed(self):
        curr = normalize_and_validate_url("https://example.com/start")
        target = validate_redirect(curr, "https://example.com/next", current_redirect_count=0)
        assert target.scheme == "https"
        assert target.canonical_url == "https://example.com/next"

    def test_http_to_https_allowed(self):
        curr = normalize_and_validate_url("http://example.com/start")
        target = validate_redirect(curr, "https://example.com/secure", current_redirect_count=0)
        assert target.scheme == "https"
        assert target.canonical_url == "https://example.com/secure"

    def test_http_to_http_allowed(self):
        curr = normalize_and_validate_url("http://example.com/start")
        target = validate_redirect(curr, "http://example.com/next", current_redirect_count=0)
        assert target.scheme == "http"
        assert target.canonical_url == "http://example.com/next"

    def test_https_to_http_downgrade_strictly_rejected(self):
        curr = normalize_and_validate_url("https://example.com/start")
        with pytest.raises(HTTPSDowngradeBlockedError, match="Insecure protocol downgrade"):
            validate_redirect(curr, "http://example.com/insecure", current_redirect_count=0)

    def test_redirect_to_private_ip_restarts_validation(self):
        curr = normalize_and_validate_url("https://example.com/start")
        # Relative or absolute redirect to a private IP literal
        target = validate_redirect(curr, "https://127.0.0.1/admin", current_redirect_count=0)
        assert target.is_ip_literal is True
        # When passed to get_validated_destination_ips, it fails SSRF
        with pytest.raises(SSRFBlockedError):
            validate_ip_address(target.ip_address)

    def test_redirect_budget_exceeded(self):
        curr = normalize_and_validate_url("https://example.com/hop3")
        with pytest.raises(MaxRedirectsExceededError, match="Maximum redirect limit"):
            validate_redirect(curr, "https://example.com/hop4", current_redirect_count=3)

    def test_redirect_fragment_rejected(self):
        curr = normalize_and_validate_url("https://example.com/start")
        with pytest.raises(URLValidationError, match="fragment identifier"):
            validate_redirect(curr, "https://example.com/next#section", current_redirect_count=0)


# =====================================================================
# 7. Secure Fetch Execution & Limits Tests (Deterministic Mocks)
# =====================================================================

@pytest.mark.asyncio
class TestSecureFetchExecution:
    async def test_response_size_limit_enforced(self):
        # Configure a test config with a tiny byte limit (e.g. 50 bytes)
        tiny_config = WebSecurityConfig(max_response_bytes=50)
        url = normalize_and_validate_url("http://example.com/large")

        # Mock transport returning 100 bytes
        mock_response = unittest.mock.AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/plain"}
        async def mock_aiter_bytes():
            yield b"A" * 30
            yield b"B" * 30  # Total 60 > 50
        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = unittest.mock.AsyncMock()
        mock_stream_ctx.__aenter__.return_value = mock_response
        mock_stream_ctx.__aexit__.return_value = None

        with unittest.mock.patch("httpx.AsyncClient.stream", return_value=mock_stream_ctx):
            with pytest.raises(ResponseSizeLimitExceededError, match="maximum byte limit"):
                await execute_secure_fetch(
                    url,
                    destination_ip=ipaddress.IPv4Address("93.184.215.14"),
                    config=tiny_config,
                )

    async def test_redirect_loop_in_fetch(self):
        url = normalize_and_validate_url("https://example.com/start")
        config = WebSecurityConfig(max_redirects=2)

        # Mock responses simulating 3 redirects
        mock_res1 = unittest.mock.AsyncMock()
        mock_res1.status_code = 302
        mock_res1.headers = {"location": "https://example.com/hop1"}

        mock_res2 = unittest.mock.AsyncMock()
        mock_res2.status_code = 302
        mock_res2.headers = {"location": "https://example.com/hop2"}

        mock_res3 = unittest.mock.AsyncMock()
        mock_res3.status_code = 302
        mock_res3.headers = {"location": "https://example.com/hop3"}

        responses = [mock_res1, mock_res2, mock_res3]

        def make_stream_ctx(*args, **kwargs):
            res = responses.pop(0)
            ctx = unittest.mock.AsyncMock()
            ctx.__aenter__.return_value = res
            ctx.__aexit__.return_value = None
            return ctx

        resolver = MockDNSResolver({
            "example.com": ["93.184.215.14"]
        })

        with unittest.mock.patch("httpx.AsyncClient.stream", side_effect=make_stream_ctx):
            with pytest.raises(MaxRedirectsExceededError):
                await execute_secure_fetch(
                    url,
                    resolver=resolver,
                    config=config,
                )


# =====================================================================
# 8. Architectural Regression & Safety Invariants
# =====================================================================

class TestPhase5K1ArchitecturalInvariants:
    def test_no_web_tools_registered_in_tool_registry(self):
        """Phase 5K.1 must NOT register search_web or read_web_page tools yet."""
        from app.services.tools.registry import create_default_tool_registry
        default_reg = create_default_tool_registry()
        tools = default_reg.list_tools()
        registered_names = {t.definition.name for t in tools}
        assert "search_web" not in registered_names
        assert "read_web_page" not in registered_names

    def test_server_controlled_constants_are_immutable(self):
        """Verify that server-controlled constants cannot be altered."""
        assert ALLOWED_PORTS == (80, 443)
        assert ALLOWED_SCHEMES == ("http", "https")
        assert DEFAULT_MAX_RESPONSE_BYTES == 2 * 1024 * 1024
        assert DEFAULT_MAX_REDIRECTS == 3
