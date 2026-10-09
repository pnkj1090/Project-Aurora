"""
SSRF (Server-Side Request Forgery) protection and IP validation module.
Blocks private, loopback, link-local, multicast, reserved, and cloud-metadata addresses.
"""
import ipaddress
from typing import Tuple, Union

from app.services.web.security.exceptions import SSRFBlockedError


# Explicitly blocked IPv4 networks per Phase 5K specification
BLOCKED_IPV4_NETWORKS: Tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),         # Current network (RFC 1122)
    ipaddress.ip_network("10.0.0.0/8"),        # Private network (RFC 1918)
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback (RFC 1122)
    ipaddress.ip_network("169.254.0.0/16"),    # Link-local / Cloud metadata (RFC 3927)
    ipaddress.ip_network("172.16.0.0/12"),     # Private network (RFC 1918)
    ipaddress.ip_network("192.168.0.0/16"),    # Private network (RFC 1918)
    ipaddress.ip_network("100.64.0.0/10"),     # Carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("192.0.0.0/24"),      # IETF Protocol Assignments (RFC 6890)
    ipaddress.ip_network("192.0.2.0/24"),      # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("198.51.100.0/24"),   # TEST-NET-2 (RFC 5737)
    ipaddress.ip_network("203.0.113.0/24"),    # TEST-NET-3 (RFC 5737)
    ipaddress.ip_network("224.0.0.0/4"),       # Multicast (RFC 5771)
    ipaddress.ip_network("240.0.0.0/4"),       # Reserved for future use (RFC 1112)
    ipaddress.ip_network("255.255.255.255/32") # Limited broadcast (RFC 919)
)

# Explicitly blocked IPv6 networks per Phase 5K specification
BLOCKED_IPV6_NETWORKS: Tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("::/128"),            # Unspecified address (RFC 4291)
    ipaddress.ip_network("::1/128"),           # Loopback address (RFC 4291)
    ipaddress.ip_network("::ffff:0:0/96"),      # IPv4-mapped IPv6 (RFC 4291)
    ipaddress.ip_network("fe80::/10"),         # Link-local unicast (RFC 4291)
    ipaddress.ip_network("fc00::/7"),          # Unique local address (RFC 4193)
    ipaddress.ip_network("ff00::/8"),          # Multicast (RFC 4291)
)


def is_ip_allowed(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> bool:
    """
    Evaluates whether an IP address is a permissible public Internet address.
    Fails closed on any ambiguity or private/reserved classification.
    """
    if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return False

    # Check standard Python built-in flags
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False

    # Check explicit IPv4 blacklist
    if isinstance(ip, ipaddress.IPv4Address):
        for net in BLOCKED_IPV4_NETWORKS:
            if ip in net:
                return False

    # Check explicit IPv6 blacklist and mapped address
    elif isinstance(ip, ipaddress.IPv6Address):
        # If IPv4-mapped address is present, also validate the mapped IPv4
        if ip.ipv4_mapped:
            if not is_ip_allowed(ip.ipv4_mapped):
                return False

        for net in BLOCKED_IPV6_NETWORKS:
            if ip in net:
                return False

    return True


def validate_ip_address(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]) -> None:
    """
    Raises SSRFBlockedError if the IP address is prohibited.
    """
    if not is_ip_allowed(ip):
        raise SSRFBlockedError(
            f"IP address '{ip}' is blocked by SSRF policy (private, loopback, or reserved destination)."
        )
