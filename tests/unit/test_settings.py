"""The production profile must refuse to start with weak or missing config."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
STRONG = "s" * 40


def _load_prod(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(SRC),
        "DJANGO_SETTINGS_MODULE": "notify_relay.settings.prod",
        "ALLOWED_HOSTS": "relay.internal",
        "SECRET_KEY": STRONG,
        "JWT_SIGNING_KEY": STRONG + "j",
        "API_KEY_HASH_PEPPER": STRONG + "p",
        "WEBHOOK_HMAC_SECRET": STRONG + "w",
        **overrides,
    }
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_prod_settings_load_with_strong_config() -> None:
    result = _load_prod()
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "overrides",
    [
        {"SECRET_KEY": ""},
        {"JWT_SIGNING_KEY": "short"},
        {"API_KEY_HASH_PEPPER": "dev-" + STRONG},
        {"WEBHOOK_HMAC_SECRET": "test-" + STRONG},
        {"ALLOWED_HOSTS": ""},
        {"ALLOWED_HOSTS": "*"},
    ],
)
def test_prod_settings_reject_weak_config(overrides: dict[str, str]) -> None:
    result = _load_prod(**overrides)
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr
