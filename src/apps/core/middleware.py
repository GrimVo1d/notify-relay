"""Request-scoped middleware.

* :class:`RequestIDMiddleware` accepts an incoming ``X-Request-ID`` header
  (or generates a fresh ULID), stores it on the request and on a context
  variable for log correlation, and echoes it on the response.
* :class:`RateLimitMiddleware` consults the Redis token bucket for the
  request's identity (API key, or remote address fallback) and short-circuits
  with ``429 Too Many Requests`` + ``Retry-After`` when the bucket is empty.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from ulid import ULID

from apps.core.models import ApiKey
from apps.core.security import split_full_key
from apps.ratelimit.bucket import TokenBucket

from .logging import reset_request_id, set_request_id

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_ID_LENGTH = 64


def _coerce_incoming(value: str) -> str | None:
    value = value.strip()
    if not value or len(value) > _MAX_ID_LENGTH:
        return None
    if not all(c.isalnum() or c in "-_" for c in value):
        return None
    return value


class RequestIDMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.META.get("HTTP_X_REQUEST_ID", "")
        request_id = _coerce_incoming(incoming) or str(ULID())

        request.request_id = request_id  # type: ignore[attr-defined]
        token = set_request_id(request_id)
        try:
            response = self.get_response(request)
        finally:
            reset_request_id(token)

        response[REQUEST_ID_HEADER] = request_id
        return response


class RateLimitMiddleware:
    """Token-bucket rate limit per API key (or per remote IP for anon traffic).

    Lookup of per-key ``rate_limit_per_min`` / ``burst`` is done by prefix —
    multiple active keys sharing a prefix get the minimum of their limits.
    Auth verification of the secret still happens later in DRF.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._bucket: TokenBucket | None = None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "RATE_LIMIT_ENABLED", True):
            return self.get_response(request)
        if not self._path_eligible(request.path):
            return self.get_response(request)

        derived = self._derive(request)
        if derived is None:
            return self.get_response(request)
        identity, rate, burst = derived

        result = self._get_bucket().consume(identity, rate_per_min=rate, burst=burst)
        if not result.allowed:
            resp = JsonResponse(
                {"detail": "rate limit exceeded"},
                status=429,
            )
            resp["Retry-After"] = str(result.retry_after_seconds)
            resp["X-RateLimit-Remaining"] = "0"
            return resp

        response = self.get_response(request)
        response["X-RateLimit-Remaining"] = str(max(0, int(result.remaining)))
        return response

    @staticmethod
    def _path_eligible(path: str) -> bool:
        if not path.startswith("/api/v1/"):
            return False
        if path.startswith("/api/v1/auth/"):
            return False
        return True

    def _derive(self, request: HttpRequest) -> tuple[str, int, int] | None:
        raw = request.META.get("HTTP_X_API_KEY", "").strip()
        if raw:
            parsed = split_full_key(raw)
            if parsed is None:
                return None
            prefix, _secret = parsed
            rate, burst = self._lookup_prefix_limit(prefix)
            identity = "k:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            return identity, rate, burst

        ip = request.META.get("REMOTE_ADDR") or "unknown"
        return (
            f"ip:{ip}",
            int(getattr(settings, "RATE_LIMIT_DEFAULT_PER_MIN", 100)),
            int(getattr(settings, "RATE_LIMIT_DEFAULT_BURST", 200)),
        )

    @staticmethod
    def _lookup_prefix_limit(prefix: str) -> tuple[int, int]:
        rows = list(
            ApiKey.objects.active().filter(prefix=prefix).values_list("rate_limit_per_min", "burst")
        )
        if not rows:
            return (
                int(getattr(settings, "RATE_LIMIT_DEFAULT_PER_MIN", 100)),
                int(getattr(settings, "RATE_LIMIT_DEFAULT_BURST", 200)),
            )
        return min(r[0] for r in rows), min(r[1] for r in rows)

    def _get_bucket(self) -> TokenBucket:
        if self._bucket is None:
            client = self._make_client()
            self._bucket = TokenBucket(client)
        return self._bucket

    @staticmethod
    def _make_client() -> Any:
        factory = getattr(settings, "RATE_LIMIT_REDIS_CLIENT_FACTORY", None)
        if callable(factory):
            return factory()
        import redis  # noqa: PLC0415

        return redis.from_url(settings.REDIS_URL)
