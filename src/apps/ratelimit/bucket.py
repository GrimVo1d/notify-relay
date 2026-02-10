"""Redis-backed token bucket used for per-API-key rate limiting.

The decrement is atomic — the Lua script reads, refills, checks and writes
in one server-side step, so concurrent calls can't oversubscribe the bucket
even under high contention. The script is loaded lazily and re-loaded on
``NoScriptError`` (i.e. after a Redis flush/restart).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import redis as redis_pkg

logger = logging.getLogger(__name__)

_SCRIPT_PATH = Path(__file__).parent / "lua" / "token_bucket.lua"


@dataclass(frozen=True)
class BucketResult:
    allowed: bool
    remaining: float
    retry_after_ms: int

    @property
    def retry_after_seconds(self) -> int:
        return max(1, (self.retry_after_ms + 999) // 1000) if not self.allowed else 0


class TokenBucket:
    def __init__(self, client: redis_pkg.Redis, *, key_prefix: str = "rl:") -> None:
        self.client = client
        self.key_prefix = key_prefix
        self._script_sha: str | None = None

    def consume(
        self,
        identity: str,
        *,
        rate_per_min: int,
        burst: int,
        tokens: int = 1,
    ) -> BucketResult:
        """Try to take ``tokens`` from ``identity``'s bucket. Always returns —
        the ``allowed`` flag tells whether the caller should proceed.
        """
        if rate_per_min <= 0 or burst <= 0 or tokens <= 0:
            raise ValueError("rate_per_min, burst, tokens must be positive")

        rate_per_sec = rate_per_min / 60.0
        now_ms = int(time.monotonic() * 1000)
        key = f"{self.key_prefix}{identity}"

        try:
            raw = self._eval(key, rate_per_sec, burst, now_ms, tokens)
        except redis_pkg.exceptions.NoScriptError:
            self._script_sha = self._load_script()
            raw = self._eval(key, rate_per_sec, burst, now_ms, tokens)

        return _parse(raw)

    def _load_script(self) -> str:
        return self.client.script_load(_SCRIPT_PATH.read_text(encoding="utf-8"))

    def _eval(
        self,
        key: str,
        rate_per_sec: float,
        burst: int,
        now_ms: int,
        tokens: int,
    ) -> Any:
        if self._script_sha is None:
            self._script_sha = self._load_script()
        return self.client.evalsha(
            self._script_sha,
            1,
            key,
            rate_per_sec,
            burst,
            now_ms,
            tokens,
        )


def _parse(raw: Any) -> BucketResult:
    allowed_raw, tokens_raw, retry_raw = raw
    allowed = bool(int(allowed_raw))
    tokens = float(tokens_raw.decode() if isinstance(tokens_raw, bytes) else tokens_raw)
    retry_ms = int(retry_raw)
    return BucketResult(allowed=allowed, remaining=tokens, retry_after_ms=retry_ms)
