"""API key generation and hashing.

A key as shown to the user has the shape ``<prefix><secret>``; the prefix is
stored in plaintext so we can look up candidate rows by prefix without
scanning, and ``secret`` is verified against ``hashed_secret`` using argon2
with a server-side pepper from ``settings.API_KEY_HASH_PEPPER``.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from passlib.hash import argon2

API_KEY_PREFIX_DEFAULT = "nr_live_"
_SECRET_NBYTES = 24


def generate_api_key(prefix: str = API_KEY_PREFIX_DEFAULT) -> tuple[str, str, str]:
    """Return ``(prefix, secret, full_key)``. The caller shows ``full_key`` once."""
    secret = secrets.token_urlsafe(_SECRET_NBYTES)
    return prefix, secret, f"{prefix}{secret}"


def split_full_key(full_key: str) -> tuple[str, str] | None:
    """Split ``<env>_<tier>_<secret>`` into ``("<env>_<tier>_", "<secret>")``.

    Returns ``None`` if the input doesn't carry the canonical 2-underscore prefix.
    The secret can itself contain underscores (it's base64-url), so we split
    on the first two separators rather than the last one.
    """
    parts = full_key.split("_", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return f"{parts[0]}_{parts[1]}_", parts[2]


def _pepper() -> str:
    pepper: str = getattr(settings, "API_KEY_HASH_PEPPER", "") or ""
    if not pepper:
        raise RuntimeError("API_KEY_HASH_PEPPER is not configured")
    return pepper


def hash_secret(secret: str) -> str:
    """Hash a key secret using argon2 + server pepper."""
    return argon2.using(rounds=4).hash(secret + _pepper())


def verify_secret(secret: str, hashed: str) -> bool:
    """Constant-time verify of ``secret`` against ``hashed``."""
    try:
        return argon2.verify(secret + _pepper(), hashed)
    except ValueError:
        return False
