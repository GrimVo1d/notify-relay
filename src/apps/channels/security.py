"""Outgoing webhook signature.

``X-Notify-Signature: sha256=<hex>`` is HMAC-SHA256 over ``"<timestamp>.<body>"``
where ``<timestamp>`` is the value of ``X-Notify-Timestamp`` (Unix seconds).
Signing the timestamp lets receivers reject replays: verify the signature,
then refuse timestamps older than a few minutes and de-duplicate on
``X-Notify-Message-Id``.
"""

from __future__ import annotations

import hashlib
import hmac

HMAC_HEADER_NAME = "X-Notify-Signature"
HMAC_TIMESTAMP_HEADER = "X-Notify-Timestamp"
MESSAGE_ID_HEADER = "X-Notify-Message-Id"

_SIGNATURE_PREFIX = "sha256="


def compute_signature(body: bytes, secret: str, timestamp: str) -> str:
    """Return the value to send in ``X-Notify-Signature``."""
    signed = timestamp.encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify_signature(body: bytes, secret: str, timestamp: str, header_value: str) -> bool:
    """Constant-time check of ``header_value``; the reference for receivers."""
    if not header_value.startswith(_SIGNATURE_PREFIX):
        return False
    return hmac.compare_digest(compute_signature(body, secret, timestamp), header_value)
