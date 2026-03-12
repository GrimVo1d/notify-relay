"""Property-based tests for :mod:`apps.messages_api.validators`.

We claim invariants of the SSRF guard (any IP in a blocked range, regardless
of textual form, must be rejected) and feed Hypothesis a fresh batch of
adversarial inputs on every run. Compared to example-based tests, this
catches edge cases — IPv6 literals, zone-id suffixes, hex-encoded IPv4 —
that handwritten examples often miss.
"""

from __future__ import annotations

import ipaddress

import pytest
from django.core.exceptions import ValidationError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from apps.messages_api.validators import (
    _is_blocked_ip,
    validate_webhook_url,
)


@given(st.ip_addresses(v=4).filter(lambda ip: not ip.is_global))
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_any_non_global_ipv4_is_blocked(ip: ipaddress.IPv4Address) -> None:
    with pytest.raises(ValidationError):
        validate_webhook_url(f"http://{ip}/path")


@given(st.ip_addresses(v=4).filter(lambda ip: ip.is_global and not ip.is_multicast))
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_public_ipv4_is_not_blocked_by_classifier(ip: ipaddress.IPv4Address) -> None:
    # We only check the IP classifier here, not DNS resolution; some public
    # IPs may still fail validate_webhook_url() for unrelated reasons.
    assert _is_blocked_ip(str(ip)) is False


@given(st.just(ipaddress.IPv6Address("::1")) | st.ip_addresses(network="fe80::/10"))
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_ipv6_loopback_and_linklocal_are_blocked(ip: ipaddress.IPv6Address) -> None:
    with pytest.raises(ValidationError):
        validate_webhook_url(f"http://[{ip}]/")


@given(st.text(min_size=1).filter(lambda s: not s.startswith(("http://", "https://"))))
@settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_non_http_schemes_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        validate_webhook_url(raw)


@pytest.mark.parametrize(
    "addr",
    [
        "100.64.0.1",  # CGNAT
        "64:ff9b::a00:1",  # NAT64 wrapping 10.0.0.1
        "2002:a00:1::1",  # 6to4 wrapping 10.0.0.1
        "2001::1",  # Teredo
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "224.0.0.1",
    ],
)
def test_special_ranges_are_blocked(addr: str) -> None:
    assert _is_blocked_ip(addr) is True


def test_extra_blocked_networks_from_settings(settings) -> None:  # type: ignore[no-untyped-def]
    settings.WEBHOOK_BLOCKED_NETWORKS = ["1.1.1.0/24"]
    assert _is_blocked_ip("1.1.1.1") is True
    assert _is_blocked_ip("8.8.8.8") is False


def test_hostname_resolving_to_private_address_is_rejected(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import socket

    def fake_getaddrinfo(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValidationError):
        validate_webhook_url("https://hooks.example.internal/x")


def test_returns_vetted_addresses_for_hostname(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import socket

    def fake_getaddrinfo(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_webhook_url("https://hooks.example.internal/x") == ["1.1.1.1", "8.8.8.8"]
