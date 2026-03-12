from __future__ import annotations

import re

import pytest

from apps.channels.security import compute_signature, verify_signature
from apps.core.security import generate_api_key, hash_secret, split_full_key, verify_secret


def test_generated_key_has_documented_shape() -> None:
    key_id, secret, full = generate_api_key()
    assert re.fullmatch(r"nr_[0-9a-f]{12}", key_id)
    assert full == f"{key_id}_{secret}"
    assert split_full_key(full) == (key_id, secret)


def test_secret_with_underscores_splits_on_first_separator() -> None:
    assert split_full_key("nr_0123456789ab_a_b_c") == ("nr_0123456789ab", "a_b_c")


@pytest.mark.parametrize(
    "raw",
    ["", "nr_", "nr_0123456789ab", "nr_0123456789ab_", "xx_0123456789ab_s", "nr_XYZ_s"],
)
def test_malformed_keys_are_rejected(raw: str) -> None:
    assert split_full_key(raw) is None


def test_hash_is_keyed_and_verifies() -> None:
    hashed = hash_secret("s3cret")
    assert hashed != "s3cret"
    assert verify_secret("s3cret", hashed)
    assert not verify_secret("other", hashed)


def test_hash_depends_on_pepper(settings) -> None:  # type: ignore[no-untyped-def]
    before = hash_secret("s3cret")
    settings.API_KEY_HASH_PEPPER = "another-pepper"
    assert hash_secret("s3cret") != before


def test_signature_covers_timestamp_and_body() -> None:
    sig = compute_signature(b'{"a":1}', "k", "1700000000")
    assert sig.startswith("sha256=")
    assert verify_signature(b'{"a":1}', "k", "1700000000", sig)
    assert not verify_signature(b'{"a":1}', "k", "1700000001", sig)
    assert not verify_signature(b'{"a":2}', "k", "1700000000", sig)
    assert not verify_signature(b'{"a":1}', "other", "1700000000", sig)
    assert not verify_signature(b'{"a":1}', "k", "1700000000", sig.removeprefix("sha256="))
