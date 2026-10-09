"""
URL normalization and security validation module for Project Aurora.
Enforces strict scheme, credential, fragment, port, and hostname rules.
"""
from dataclasses import dataclass
import ipaddress
import posixpath
import re
import socket
from typing import Optional, Union
import urllib.parse

import idna

from app.services.web.security.config import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    WebSecurityConfig,
)
from app.services.web.security.exceptions import URLValidationError


_ASCII_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)


@dataclass(frozen=True)
class ValidatedURL:
    """
    Structured, fully validated URL representation.
    Guarantees no userinfo, no fragment, allowed scheme and port, and canonical hostname.
    """
    raw_url: str
    scheme: str
    hostname: str
    port: int
    path: str
    query: str
    canonical_url: str
    is_ip_literal: bool
    ip_address: Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]] = None


def parse_ip_literal(host: str) -> Optional[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]]:
    """
    Safely parse IP literals in diverse standard and legacy representations:
    - Standard IPv4 / IPv6
    - IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
    - Pure integer/dword IPv4 (e.g. 2130706433)
    - Hexadecimal dword IPv4 (e.g. 0x7f000001)
    - Dotted octal/hex/decimal IPv4 (e.g. 0177.0.0.1, 0x7f.0.0.1, 127.1)
    
    Returns an IPv4Address or IPv6Address if host is an IP literal, or None otherwise.
    """
    cleaned = host.strip("[]").strip()
    if not cleaned:
        return None

    # 1. Standard ipaddress parser (handles standard IPv6 and canonical IPv4)
    try:
        ip = ipaddress.ip_address(cleaned)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            return ip.ipv4_mapped
        return ip
    except ValueError:
        pass

    # 2. Pure integer/dword decimal IPv4
    if cleaned.isdigit():
        try:
            val = int(cleaned, 10)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass

    # 3. Pure hexadecimal dword IPv4 (0x...)
    if cleaned.lower().startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in cleaned[2:]):
        try:
            val = int(cleaned, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except ValueError:
            pass

    # 4. Dotted representations (decimal, octal, hex, multi-part)
    parts = cleaned.split(".")
    if 1 <= len(parts) <= 4:
        is_numeric = True
        for p in parts:
            if p.isdigit():
                continue
            if p.lower().startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in p[2:]):
                continue
            is_numeric = False
            break
        
        if is_numeric:
            # First try OS inet_aton if available
            try:
                packed = socket.inet_aton(cleaned)
                return ipaddress.IPv4Address(packed)
            except (OSError, ValueError):
                pass

            # Pure Python arithmetic fallback for multi-part base 8/10/16
            try:
                vals = []
                for p in parts:
                    if p.lower().startswith("0x"):
                        v = int(p, 16)
                    elif p.startswith("0") and len(p) > 1 and p.isdigit():
                        v = int(p, 8)
                    else:
                        v = int(p, 10)
                    vals.append(v)
                
                # Check ranges and assemble 32-bit int
                if len(vals) == 4:
                    if all(0 <= v <= 255 for v in vals):
                        int_val = (vals[0] << 24) | (vals[1] << 16) | (vals[2] << 8) | vals[3]
                        return ipaddress.IPv4Address(int_val)
                elif len(vals) == 3:
                    if 0 <= vals[0] <= 255 and 0 <= vals[1] <= 255 and 0 <= vals[2] <= 65535:
                        int_val = (vals[0] << 24) | (vals[1] << 16) | vals[2]
                        return ipaddress.IPv4Address(int_val)
                elif len(vals) == 2:
                    if 0 <= vals[0] <= 255 and 0 <= vals[1] <= 16777215:
                        int_val = (vals[0] << 24) | vals[1]
                        return ipaddress.IPv4Address(int_val)
                elif len(vals) == 1:
                    if 0 <= vals[0] <= 0xFFFFFFFF:
                        return ipaddress.IPv4Address(vals[0])
            except (ValueError, OverflowError):
                pass

    return None


def normalize_and_validate_url(
    raw_url: str,
    config: Optional[WebSecurityConfig] = None,
) -> ValidatedURL:
    """
    Deterministically normalizes and validates a target URL before any network operation.

    Rules enforced:
    - Scheme must be http or https only.
    - Userinfo/credentials (user:pass@host) are rejected.
    - URL fragments (#...) are rejected (never silently stripped).
    - Explicit port must be 80 or 443 only; all other explicit ports are rejected.
    - Hostname must be non-empty, free of control characters/whitespace, and syntactically valid.
    - Unicode hostnames undergo safe IDNA UTS #46 encoding to canonical ASCII/punycode.
    - IP literals (decimal, octal, hex, dword, IPv6, IPv4-mapped) are normalized.
    - Produces a single canonical URL for cache keying and citation attribution.
    - Note: IDNA normalization guarantees syntactic validity and punycode canonicalization;
      generic visual-homograph detection is explicitly out of scope.
    """
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise URLValidationError("URL must be a non-empty string.")

    # Reject raw control characters (including \r, \n, \t, \0) before urllib.parse strips them
    if any(ord(c) < 32 or ord(c) == 127 for c in raw_url):
        raise URLValidationError("URL contains forbidden control characters or whitespace.")

    url = raw_url.strip()

    # Reject fragments immediately: never silently strip
    if "#" in url:
        raise URLValidationError("URL contains a fragment identifier ('#'), which is prohibited.")

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception as exc:
        raise URLValidationError(f"Malformed URL string: {exc}") from exc

    # Validate scheme
    scheme = parsed.scheme.lower()
    allowed_schemes = config.allowed_schemes if config else ALLOWED_SCHEMES
    if scheme not in allowed_schemes:
        raise URLValidationError(
            f"Unsupported URL scheme '{parsed.scheme}'. Only {list(allowed_schemes)} are allowed."
        )

    # Reject userinfo/credentials
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise URLValidationError("URL contains user credentials/userinfo, which are prohibited.")

    # Validate port
    allowed_ports = config.allowed_ports if config else ALLOWED_PORTS
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise URLValidationError(f"Invalid URL port specified: {exc}") from exc

    if explicit_port is not None:
        if explicit_port not in allowed_ports:
            raise URLValidationError(
                f"Destination port {explicit_port} is not allowed. Only ports {list(allowed_ports)} are permitted."
            )
        resolved_port = explicit_port
    else:
        resolved_port = 80 if scheme == "http" else 443

    # Validate hostname
    raw_host = parsed.hostname
    if not raw_host:
        raise URLValidationError("URL does not contain a valid hostname.")

    # Reject control characters, whitespace, and null bytes
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in raw_host):
        raise URLValidationError("Hostname contains control characters or whitespace.")
    if "\0" in raw_host:
        raise URLValidationError("Hostname contains null bytes.")

    # Check for IP literal
    ip_literal = parse_ip_literal(raw_host)
    if ip_literal is not None:
        is_ip_literal = True
        ip_address = ip_literal
        if isinstance(ip_literal, ipaddress.IPv6Address):
            canonical_hostname = f"[{ip_literal}]"
        else:
            canonical_hostname = str(ip_literal)
    else:
        is_ip_literal = False
        ip_address = None

        # Safe IDNA normalization (UTS #46)
        hostname_to_encode = raw_host.rstrip(".")
        if not hostname_to_encode:
            raise URLValidationError("Hostname cannot be empty or a solitary dot.")

        try:
            encoded_bytes = idna.encode(hostname_to_encode, uts46=True)
            canonical_hostname = encoded_bytes.decode("ascii").lower()
        except (idna.IDNAError, UnicodeError, ValueError) as exc:
            raise URLValidationError(f"Invalid hostname IDNA encoding: {exc}") from exc

        # Validate canonical hostname label syntax
        if len(canonical_hostname) > 253:
            raise URLValidationError("Hostname exceeds maximum allowed length of 253 characters.")

        labels = canonical_hostname.split(".")
        if not labels or any(not label for label in labels):
            raise URLValidationError("Hostname contains empty domain labels.")

        for label in labels:
            if len(label) > 63:
                raise URLValidationError(f"Domain label '{label}' exceeds maximum length of 63 characters.")
            if not _ASCII_LABEL_RE.match(label):
                raise URLValidationError(f"Domain label '{label}' contains invalid characters.")

    # Normalize path: collapse duplicate slashes and resolve relative segments safely
    raw_path = parsed.path or "/"
    trailing_slash = raw_path.endswith("/") and len(raw_path) > 1
    normalized_path = posixpath.normpath(raw_path)
    if not normalized_path.startswith("/"):
        normalized_path = "/" + normalized_path
    if trailing_slash and not normalized_path.endswith("/"):
        normalized_path += "/"

    # Normalize query: sort keys and values deterministically
    if parsed.query:
        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query_pairs.sort(key=lambda pair: (pair[0], pair[1]))
        canonical_query = urllib.parse.urlencode(query_pairs)
    else:
        canonical_query = ""

    # Build canonical URL representation
    default_port = 80 if scheme == "http" else 443
    port_part = f":{resolved_port}" if resolved_port != default_port else ""
    query_part = f"?{canonical_query}" if canonical_query else ""
    canonical_url = f"{scheme}://{canonical_hostname}{port_part}{normalized_path}{query_part}"

    return ValidatedURL(
        raw_url=raw_url,
        scheme=scheme,
        hostname=canonical_hostname,
        port=resolved_port,
        path=normalized_path,
        query=canonical_query,
        canonical_url=canonical_url,
        is_ip_literal=is_ip_literal,
        ip_address=ip_address,
    )


# Alias for backwards and cross-module compatibility
validate_and_normalize_url = normalize_and_validate_url

