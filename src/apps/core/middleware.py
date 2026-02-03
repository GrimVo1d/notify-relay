"""Request-scoped middleware.

* :class:`RequestIDMiddleware` accepts an incoming ``X-Request-ID`` header
  (or generates a fresh ULID), stores it on the request and on a context
  variable for log correlation, and echoes it on the response.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from ulid import ULID

from .logging import reset_request_id, set_request_id

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
