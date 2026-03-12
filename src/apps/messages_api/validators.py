"""Validation of message inputs.

Two concerns live here:

* :func:`validate_recipient` dispatches on channel — email goes through
  Django's :class:`EmailValidator`; webhook goes through :func:`validate_webhook_url`.
* :func:`validate_webhook_url` is the SSRF guard. It refuses non-http(s)
  schemes, missing hostnames, and any address that is not globally routable
  (private, loopback, link-local, CGNAT, reserved, multicast, IPv6 tunnels
  that can embed private IPv4, plus ``WEBHOOK_BLOCKED_NETWORKS``). It returns
  the vetted addresses so the sender can connect to exactly those and a
  second DNS answer can't redirect it (DNS rebinding).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

from apps.templating.models import Channel

_email_validator = EmailValidator()

# IPv6 ranges that wrap an IPv4 address (NAT64, 6to4, Teredo). ``is_global``
# treats some of them as public, but they can reach private IPv4 targets.
_TUNNEL_NETWORKS = tuple(
    ipaddress.ip_network(n) for n in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)


def validate_recipient(channel: str, recipient: str) -> None:
    if channel == Channel.EMAIL:
        _email_validator(recipient)
        return
    if channel == Channel.WEBHOOK:
        validate_webhook_url(recipient)
        return
    raise ValidationError(f"unknown channel: {channel}")


def _blocked_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    raw = getattr(settings, "WEBHOOK_BLOCKED_NETWORKS", []) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    return [ipaddress.ip_network(n.strip(), strict=False) for n in raw if n.strip()]


def _is_blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    if not ip.is_global or ip.is_multicast:
        return True
    if any(ip in net for net in _TUNNEL_NETWORKS):
        return True
    return any(ip in net for net in _blocked_networks())


def validate_webhook_url(url: str) -> list[str]:
    """Validate ``url`` and return the IP addresses it may be delivered to."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("webhook URL must use http(s)")
    host = parsed.hostname
    if not host:
        raise ValidationError("webhook URL must include a hostname")

    if _looks_like_ip(host):
        if _is_blocked_ip(host):
            raise ValidationError("webhook URL points to a blocked address range")
        return [host]

    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValidationError(f"webhook URL hostname does not resolve: {exc}") from exc

    addresses: list[str] = []
    for _fam, _t, _p, _c, sockaddr in infos:
        addr = str(sockaddr[0])
        if _is_blocked_ip(addr):
            raise ValidationError("webhook URL resolves to a blocked address range")
        if addr not in addresses:
            addresses.append(addr)
    if not addresses:
        raise ValidationError("webhook URL hostname does not resolve")
    return addresses


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True
