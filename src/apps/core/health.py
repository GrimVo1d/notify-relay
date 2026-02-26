"""Liveness and readiness probes.

* ``/health/live`` — process is alive, always 200. Used by orchestrators
  to decide if the container should be restarted.
* ``/health/ready`` — process is ready to serve traffic. Checks the DB and
  Redis are reachable. Returns 503 if any dependency is down so the load
  balancer takes the instance out of rotation without killing it.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import connections
from django.http import HttpRequest, JsonResponse


def live(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def ready(_request: HttpRequest) -> JsonResponse:
    checks: dict[str, str] = {}
    overall_ok = True

    if not _check_db(checks):
        overall_ok = False
    if not _check_redis(checks):
        overall_ok = False

    payload: dict[str, Any] = {
        "status": "ok" if overall_ok else "degraded",
        "checks": checks,
    }
    return JsonResponse(payload, status=200 if overall_ok else 503)


def _check_db(checks: dict[str, str]) -> bool:
    try:
        with connections["default"].cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        checks["db"] = "ok"
        return True
    except Exception as exc:  # noqa: BLE001
        checks["db"] = f"error: {exc!r}"
        return False


def _check_redis(checks: dict[str, str]) -> bool:
    try:
        client = _make_redis()
        client.ping()
        checks["redis"] = "ok"
        return True
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc!r}"
        return False


def _make_redis() -> Any:
    factory = getattr(settings, "RATE_LIMIT_REDIS_CLIENT_FACTORY", None)
    if callable(factory):
        return factory()
    import redis  # noqa: PLC0415

    return redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
