"""Validation of message inputs.

Two concerns live here:

* :func:`validate_recipient` dispatches on channel — email goes through
  Django's :class:`EmailValidator`; webhook goes through :func:`validate_webhook_url`.
* :func:`validate_webhook_url` is the SSRF guard. It refuses non-http(s)
  schemes, missing hostnames, and any address that is not globally routable
  (private, loopback, link-local, CGNAT, reserved, multicast, IPv6 tunnels
  that can embed private IPv4, plus ``WEBHOOK_BLOCKED_NETWORKS``). It returns
  the vetted addresses so the sender can connect to exactly those and a
  second DNS answer can't redirect it (DNS rebinding). Host names are
  checked in their IDNA 2008 A-label form, the same name the sender puts in
  ``Host`` and TLS SNI.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

from apps.templating.models import Channel

_email_validator = EmailValidator()

# IPv6 ranges that wrap an IPv4 address (NAT64, 6to4, Teredo, IPv4-compatible,
# SIIT) or are deprecated site-local. ``is_global`` treats some of them as
# public, but they can reach private targets on hosts that route them.
_TUNNEL_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2002::/16",
        "2001::/32",
        "::/96",
        "::ffff:0:0:0/96",
        "fec0::/10",
    )
)

# The API resolves webhook hosts while the caller waits; a name server that
# never answers must not hold a request worker for glibc's full retry cycle.
# A lookup that runs late, or can't start because slow lookups hold every
# slot, is skipped: the worker checks the URL again before sending, and that
# check is the one that decides where the request goes.
API_RESOLVE_TIMEOUT_S = 2.0
_RESOLVER_THREADS = 4
_resolver_pool = ThreadPoolExecutor(max_workers=_RESOLVER_THREADS, thread_name_prefix="webhook-dns")
_resolver_slots = threading.BoundedSemaphore(_RESOLVER_THREADS)


def validate_recipient(channel: str, recipient: str) -> None:
    if channel == Channel.EMAIL:
        _email_validator(recipient)
        return
    if channel == Channel.WEBHOOK:
        validate_webhook_url(recipient, resolve_timeout=API_RESOLVE_TIMEOUT_S)
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


def validate_webhook_url(url: str, *, resolve_timeout: float | None = None) -> list[str]:
    """Validate ``url`` and return the IP addresses it may be delivered to.

    ``resolve_timeout`` bounds the DNS lookup; ``None`` waits for the resolver.
    With a timeout, a lookup that doesn't finish in time returns ``[]``: the
    host is then only checked at send time.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise ValidationError(f"webhook URL is malformed: {exc}") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("webhook URL must use http(s)")
    host = parsed.raw_host.decode("ascii")
    if not host:
        raise ValidationError("webhook URL must include a hostname")
    port = parsed.port
    if port is not None and not 0 < port < 65536:
        raise ValidationError("webhook URL port is out of range")

    if "%" in host:
        raise ValidationError("webhook URL must not carry an IPv6 zone id")
    if _looks_like_ip(host):
        if _is_blocked_ip(host):
            raise ValidationError("webhook URL points to a blocked address range")
        return [host]

    try:
        infos = _getaddrinfo(host, port, resolve_timeout)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValidationError(f"webhook URL hostname does not resolve: {exc}") from exc
    if infos is None:
        return []

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


def _getaddrinfo(host: str, port: int | None, timeout: float | None) -> list[Any] | None:
    """``None`` means "no answer in time" (or no free slot); never queues."""
    if timeout is None:
        return socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    if not _resolver_slots.acquire(blocking=False):
        return None

    def lookup() -> list[Any]:
        try:
            return socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        finally:
            _resolver_slots.release()

    future = _resolver_pool.submit(lookup)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError:
        return None


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True
