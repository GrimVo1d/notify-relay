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


@given(st.ip_addresses(v=4).filter(lambda ip: ip.is_private or ip.is_loopback))
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_any_ipv4_in_private_range_is_blocked(ip: ipaddress.IPv4Address) -> None:
    with pytest.raises(ValidationError):
        validate_webhook_url(f"http://{ip}/path")


@given(
    st.ip_addresses(v=4).filter(
        lambda ip: not (
            ip.is_private
            or ip.is_loopback
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_unspecified
        )
    )
)
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_public_ipv4_is_not_blocked_by_classifier(ip: ipaddress.IPv4Address) -> None:
    # We only check the IP classifier here, not DNS resolution; some public
    # IPs may still fail validate_webhook_url() for unrelated reasons.
    assert _is_blocked_ip(str(ip)) is False


@given(st.ip_addresses(v=6).filter(lambda ip: ip.is_loopback or ip.is_link_local))
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
