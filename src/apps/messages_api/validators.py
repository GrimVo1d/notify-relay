"""Validation of message inputs.

Two concerns live here:

* :func:`validate_recipient` dispatches on channel — email goes through
  Django's :class:`EmailValidator`; webhook goes through :func:`validate_webhook_url`.
* :func:`validate_webhook_url` is the SSRF guard. It refuses non-http(s)
  schemes, missing hostnames, literal IPs in blocked ranges, and hostnames
  that resolve to blocked ranges. Resolution is synchronous and bounded by
  the system DNS resolver — for high-volume scenarios consider caching.
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
        raw = [x.strip() for x in raw.split(",") if x.strip()]
    return [ipaddress.ip_network(n, strict=False) for n in raw]


def _is_blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_private
    ):
        return True
    return any(ip in net for net in _blocked_networks())


def validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("webhook URL must use http(s)")
    host = parsed.hostname
    if not host:
        raise ValidationError("webhook URL must include a hostname")

    if _looks_like_ip(host):
        if _is_blocked_ip(host):
            raise ValidationError("webhook URL points to a blocked address range")
        return

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValidationError(f"webhook URL hostname does not resolve: {exc}") from exc

    for _fam, _t, _p, _c, sockaddr in infos:
        addr = sockaddr[0]
        if _is_blocked_ip(addr):
            raise ValidationError("webhook URL resolves to a blocked address range")


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True
