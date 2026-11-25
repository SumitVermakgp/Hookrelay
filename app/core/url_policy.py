"""Destination URL policy (SSRF defence).

Rules:
- Only http/https, no credentials, valid host and port.
- Non-allowlisted hosts must use https.
- Every address the host resolves to must be globally routable. Private,
  loopback, link-local, reserved, multicast and tunnelling ranges are blocked.
- Hosts in the explicit test allowlist skip the address check. The allowlist is
  matched on host name only (port is ignored) and exists for local fixtures.

The function returns the addresses it validated: [] for an allowlisted host
(connect by name), otherwise the resolved addresses. Delivery validates again at
send time and connects to one of the returned addresses, so a host that changes
its DNS answer after registration cannot redirect traffic to a blocked address.
"""

import ipaddress
import socket
from collections.abc import Callable, Collection
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048

Resolver = Callable[[str], list[str]]

_EXTRA_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
    )
)


class UrlPolicyError(ValueError):
    """Raised with a fixed, non-sensitive reason when a URL is rejected."""


def default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        raise UrlPolicyError("host could not be resolved") from None
    addresses: list[str] = []
    for info in infos:  # keep the OS ordering (it prefers reachable families); drop duplicates
        address = str(info[4][0]).split("%")[0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def is_blocked_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in net for net in _EXTRA_BLOCKED):
            return True
    return (not ip.is_global) or ip.is_multicast


def validate_destination_url(
    url: str,
    allowed_hosts: Collection[str] = (),
    resolver: Resolver = default_resolver,
) -> list[str]:
    if len(url) > MAX_URL_LENGTH:
        raise UrlPolicyError("url too long")
    if any(c.isspace() or ord(c) < 32 or c == "\\" for c in url):
        raise UrlPolicyError("url contains invalid characters")
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        raise UrlPolicyError("url is malformed") from None
    if parts.scheme not in ("http", "https"):
        raise UrlPolicyError("scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise UrlPolicyError("credentials in url are not allowed")
    if not parts.hostname:
        raise UrlPolicyError("url has no host")

    host = parts.hostname.lower().rstrip(".")
    try:
        # Internationalised names are handled in their ASCII (punycode) form everywhere:
        # allowlist match, resolution, Host header and TLS server name.
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UrlPolicyError("host is not valid") from None
    if host in {h.lower() for h in allowed_hosts}:
        return []
    if parts.scheme != "https":
        raise UrlPolicyError("https is required for this destination")

    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        addresses = resolver(host)
    if not addresses:
        raise UrlPolicyError("host could not be resolved")
    if any(is_blocked_ip(a) for a in addresses):
        raise UrlPolicyError("destination address is not allowed")
    return addresses
