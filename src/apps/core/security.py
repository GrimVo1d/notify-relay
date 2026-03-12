"""API key generation and hashing.

A key as shown to the user is ``nr_<key_id>_<secret>``. ``nr_<key_id>`` is
stored in plaintext as a unique, indexed lookup id, so authentication touches
exactly one row. The secret is 192 random bits, so a fast keyed hash is
enough: we store ``HMAC-SHA256(pepper, secret)`` with the pepper taken from
``settings.API_KEY_HASH_PEPPER`` and compare in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from django.conf import settings

KEY_PREFIX = "nr_"
_KEY_ID_NBYTES = 6
_SECRET_NBYTES = 24
_KEY_ID_RE = re.compile(rf"^{KEY_PREFIX}[0-9a-f]{{{_KEY_ID_NBYTES * 2}}}$")


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(key_id, secret, full_key)``. The caller shows ``full_key`` once."""
    key_id = f"{KEY_PREFIX}{secrets.token_hex(_KEY_ID_NBYTES)}"
    secret = secrets.token_urlsafe(_SECRET_NBYTES)
    return key_id, secret, f"{key_id}_{secret}"


def split_full_key(full_key: str) -> tuple[str, str] | None:
    """Split ``nr_<key_id>_<secret>`` into ``("nr_<key_id>", "<secret>")``.

    Returns ``None`` for anything that doesn't have that shape. The secret is
    base64-url and may itself contain underscores, so only the first separator
    after the key id counts.
    """
    if not full_key.startswith(KEY_PREFIX):
        return None
    key_id, sep, secret = full_key[len(KEY_PREFIX) :].partition("_")
    key_id = f"{KEY_PREFIX}{key_id}"
    if not sep or not secret or not _KEY_ID_RE.match(key_id):
        return None
    return key_id, secret


def _pepper() -> bytes:
    pepper: str = getattr(settings, "API_KEY_HASH_PEPPER", "") or ""
    if not pepper:
        raise RuntimeError("API_KEY_HASH_PEPPER is not configured")
    return pepper.encode("utf-8")


def hash_secret(secret: str) -> str:
    """Keyed hash of a key secret, hex-encoded."""
    return hmac.new(_pepper(), secret.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_secret(secret: str, hashed: str) -> bool:
    """Constant-time check of ``secret`` against a stored ``hash_secret`` value."""
    return hmac.compare_digest(hash_secret(secret), hashed)
