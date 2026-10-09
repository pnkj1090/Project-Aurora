"""
DNS resolution and all-or-nothing IP validation module.
Guarantees that all resolved A/AAAA records are public Internet IPs before any connection is attempted.
"""
from abc import ABC, abstractmethod
import asyncio
import ipaddress
import socket
from typing import List, Optional, Union

from app.services.web.security.config import DEFAULT_CONNECT_TIMEOUT_SECONDS
from app.services.web.security.exceptions import DNSResolutionError, SSRFBlockedError
from app.services.web.security.ssrf import is_ip_allowed, validate_ip_address
from app.services.web.security.url import ValidatedURL


class BaseDNSResolver(ABC):
    """
    Abstract interface for DNS resolution to allow deterministic test injection.
    """
    @abstractmethod
    async def resolve(self, hostname: str) -> List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
        """
        Resolves all A (IPv4) and AAAA (IPv6) records for the given canonical hostname.
        """
        pass


class SystemDNSResolver(BaseDNSResolver):
    """
    Production DNS resolver using the system getaddrinfo via asyncio.
    """
    async def resolve(self, hostname: str) -> List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
        loop = asyncio.get_running_loop()
        try:
            # Query TCP stream records for both IPv4 and IPv6
            addr_info = await loop.getaddrinfo(
                hostname,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as exc:
            raise DNSResolutionError(f"DNS lookup failed for '{hostname}': {exc}") from exc
        except Exception as exc:
            raise DNSResolutionError(f"Unexpected error resolving '{hostname}': {exc}") from exc

        resolved_ips: List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]] = []
        seen = set()

        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            if ip_str in seen:
                continue
            seen.add(ip_str)
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                resolved_ips.append(ip_obj)
            except ValueError:
                continue

        if not resolved_ips:
            raise DNSResolutionError(f"No IP addresses resolved for hostname '{hostname}'.")

        return resolved_ips


async def resolve_and_validate_hostname(
    hostname: str,
    resolver: Optional[BaseDNSResolver] = None,
    timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    """
    Resolves hostname and enforces the all-or-nothing SSRF validation policy.
    
    If ANY resolved IP is private, loopback, or blocked, the ENTIRE result is rejected.
    Fails closed on timeouts, empty results, or network errors.
    """
    res = resolver if resolver is not None else SystemDNSResolver()

    try:
        resolved_ips = await asyncio.wait_for(res.resolve(hostname), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise DNSResolutionError(
            f"DNS resolution for '{hostname}' timed out after {timeout_seconds}s."
        ) from exc
    except DNSResolutionError:
        raise
    except Exception as exc:
        raise DNSResolutionError(f"DNS resolution failed for '{hostname}': {exc}") from exc

    if not resolved_ips:
        raise DNSResolutionError(f"No DNS records returned for hostname '{hostname}'.")

    # All-or-nothing invariant: Every single IP in the result set must be allowed
    for ip in resolved_ips:
        if not is_ip_allowed(ip):
            raise SSRFBlockedError(
                f"DNS resolution for '{hostname}' returned prohibited IP '{ip}'. "
                f"Entire request rejected by all-or-nothing SSRF policy."
            )

    return resolved_ips


async def get_validated_destination_ips(
    validated_url: ValidatedURL,
    resolver: Optional[BaseDNSResolver] = None,
    timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> List[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    """
    Retrieves and validates destination IP addresses for a ValidatedURL.
    For IP literals, bypasses DNS lookup and validates the IP directly.
    For hostnames, resolves all A/AAAA records and validates each one.
    """
    if validated_url.is_ip_literal and validated_url.ip_address is not None:
        validate_ip_address(validated_url.ip_address)
        return [validated_url.ip_address]

    return await resolve_and_validate_hostname(
        validated_url.hostname,
        resolver=resolver,
        timeout_seconds=timeout_seconds,
    )
