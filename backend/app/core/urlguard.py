"""Outbound-URL guard for admin-configured targets (catalogue base URL, webhooks).

An admin may point the catalogue importer or a webhook at any `http(s)://` URL; without a guard
that is a server-side request forgery from the API container into the compose network
(`mediamtx:9997`, `postgres:5432`), the VM's LAN or cloud metadata (`169.254.169.254`).

Rule (CONTRACT Amendments 2026-09-05): loopback, link-local, RFC 1918 / ULA, multicast,
reserved addresses and single-label hostnames (compose service names) are rejected unless
`ALLOW_PRIVATE_URLS=1` **or** the mock sandbox is enabled (`MOCK_SANDBOX=1`, the laptop demo,
whose catalogue and webhook sink live at `http://api:8000/...`). The check runs when the value is
saved *and* when it is used, and hostnames are resolved so `evil.example` → `10.0.0.5` is caught.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import settings


def private_urls_allowed() -> bool:
    return bool(settings.ALLOW_PRIVATE_URLS or settings.MOCK_SANDBOX)


def _address_is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None and _address_is_private(ip.ipv4_mapped))
    )


def host_is_private(host: str, resolve: bool = True) -> bool:
    """True for literal private/loopback/link-local IPs, single-label names (`api`, `mediamtx`,
    `localhost`) and — when `resolve` — hostnames that resolve to such an address."""
    h = (host or "").strip().strip("[]").lower()
    if not h:
        return True
    try:
        return _address_is_private(ipaddress.ip_address(h))
    except ValueError:
        pass
    if "." not in h or h.endswith(".local") or h.endswith(".internal") or h.endswith(".localhost"):
        return True
    if not resolve:
        return False
    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return False  # unresolvable: the request itself will fail; not an internal target
    for info in infos:
        try:
            if _address_is_private(ipaddress.ip_address(info[4][0])):
                return True
        except ValueError:
            continue
    return False


def check_outbound_url(url: str, allow_private: bool | None = None, resolve: bool = True) -> str:
    """Validate an admin-supplied outbound URL; returns it stripped or raises ValueError."""
    s = str(url or "").strip()
    parts = urlsplit(s)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValueError("must be an http(s) URL")
    if parts.username or parts.password:
        raise ValueError("credentials in the URL are not allowed; use the auth settings")
    allowed = private_urls_allowed() if allow_private is None else allow_private
    if not allowed and host_is_private(parts.hostname or "", resolve=resolve):
        raise ValueError("private, loopback, link-local or internal hosts are not allowed (set ALLOW_PRIVATE_URLS=1 to permit)")
    return s
