"""Outgoing webhook signature: HMAC-SHA256 over the raw body, hex-encoded.

The recipient is expected to verify the signature against the body bytes
they received, using a shared secret they configured out-of-band.
"""

from __future__ import annotations

import hashlib
import hmac

HMAC_HEADER_NAME = "X-Notify-Signature"
HMAC_TIMESTAMP_HEADER = "X-Notify-Timestamp"
MESSAGE_ID_HEADER = "X-Notify-Message-Id"

_SIGNATURE_PREFIX = "sha256="


def compute_signature(body: bytes, secret: str) -> str:
    """Return the value to send in ``X-Notify-Signature``."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify_signature(body: bytes, secret: str, header_value: str) -> bool:
    """Constant-time verify ``header_value`` against the expected signature."""
    if not header_value or not header_value.startswith(_SIGNATURE_PREFIX):
        return False
    expected = compute_signature(body, secret)
    return hmac.compare_digest(expected, header_value)
