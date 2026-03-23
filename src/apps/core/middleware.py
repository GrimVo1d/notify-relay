"""Request-scoped middleware.

* :class:`RequestIDMiddleware` accepts an incoming ``X-Request-ID`` header
  (or generates a fresh ULID), stores it on the request and on a context
  variable for log correlation, and echoes it on the response.
* :class:`RateLimitMiddleware` consults the Redis token bucket for the
  request's identity and short-circuits with ``429 Too Many Requests`` +
  ``Retry-After`` when the bucket is empty. A request with a valid API key
  uses that key's bucket and limits; everything else (no key, unknown or
  wrong key) is limited per client IP. Login endpoints get their own, much
  stricter per-IP bucket. If Redis is unreachable the request is let through
  (fail open) and a warning is logged.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Callable
from typing import Any

import redis
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.log import log_response
from ulid import ULID

from apps.ratelimit.bucket import TokenBucket

from .authentication import resolve_api_key
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
            # Django logs 4xx/5xx responses after the middleware chain, when the
            # request id is gone; log them here so the line carries it.
            if response.status_code >= 400:
                log_response(
                    "%s: %s",
                    response.reason_phrase,
                    request.path,
                    response=response,
                    request=request,
                )
        finally:
            reset_request_id(token)

        response[REQUEST_ID_HEADER] = request_id
        return response


_AUTH_PATHS = ("/api/v1/auth/", "/admin/login/")


def _client_ip(request: HttpRequest) -> str:
    """Client address for per-IP buckets.

    Behind ``NUM_PROXIES`` trusted reverse proxies the client is the entry that
    many hops from the right of ``X-Forwarded-For``; entries further left are
    client-supplied and can't be trusted.
    """
    hops = int(getattr(settings, "NUM_PROXIES", 0))
    if hops > 0:
        forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", ""))
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= hops:
            # Garbage would give the sender a fresh bucket per request.
            bucket = _bucket_address(chain[-hops])
            if bucket is not None:
                return bucket
    remote = str(request.META.get("REMOTE_ADDR") or "")
    return _bucket_address(remote) or remote or "unknown"


def _bucket_address(raw: str) -> str | None:
    """The bucket key for an address: IPv4 as is, IPv6 by its /64.

    One IPv6 host usually owns a whole /64 and could pick a new source address
    per request; zone ids are dropped for the same reason.
    """
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


class RateLimitMiddleware:
    """Token-bucket rate limit per API key, per client IP, and per IP on login paths."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._bucket: TokenBucket | None = None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not getattr(settings, "RATE_LIMIT_ENABLED", True):
            return self.get_response(request)

        derived = self._derive(request)
        if derived is None:
            return self.get_response(request)
        identity, rate, burst = derived

        try:
            result = self._get_bucket().consume(identity, rate_per_min=rate, burst=burst)
        except redis.RedisError:
            logger.warning("rate limiter unavailable, letting the request through", exc_info=True)
            return self.get_response(request)
        if not result.allowed:
            resp = JsonResponse(
                {"code": "rate_limited", "detail": "rate limit exceeded"},
                status=429,
            )
            resp["Retry-After"] = str(result.retry_after_seconds)
            resp["X-RateLimit-Remaining"] = "0"
            return resp

        response = self.get_response(request)
        response["X-RateLimit-Remaining"] = str(max(0, int(result.remaining)))
        return response

    @staticmethod
    def _derive(request: HttpRequest) -> tuple[str, int, int] | None:
        path = request.path
        ip = _client_ip(request)
        if path.startswith(_AUTH_PATHS):
            return (
                f"auth-ip:{ip}",
                int(getattr(settings, "RATE_LIMIT_AUTH_PER_MIN", 10)),
                int(getattr(settings, "RATE_LIMIT_AUTH_BURST", 10)),
            )
        if not path.startswith("/api/v1/"):
            return None

        api_key = resolve_api_key(request)
        if api_key is not None:
            return f"key:{api_key.pk}", api_key.rate_limit_per_min, api_key.burst
        return (
            f"ip:{ip}",
            int(getattr(settings, "RATE_LIMIT_DEFAULT_PER_MIN", 100)),
            int(getattr(settings, "RATE_LIMIT_DEFAULT_BURST", 200)),
        )

    def _get_bucket(self) -> TokenBucket:
        if self._bucket is None:
            self._bucket = TokenBucket(self._make_client())
        return self._bucket

    @staticmethod
    def _make_client() -> Any:
        factory = getattr(settings, "RATE_LIMIT_REDIS_CLIENT_FACTORY", None)
        if callable(factory):
            return factory()
        return redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
