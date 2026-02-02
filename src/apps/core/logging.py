"""JSON logging with per-request correlation id.

The active request id is stored in a ``ContextVar`` so it survives across
``await`` boundaries and worker threads; both web requests and Celery tasks
can set it. The :class:`JsonFormatter` injects it into every log record.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

try:
    from pythonjsonlogger.json import JsonFormatter as _BaseJsonFormatter
except ImportError:  # pragma: no cover — older pythonjsonlogger
    from pythonjsonlogger.jsonlogger import JsonFormatter as _BaseJsonFormatter

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(value: str | None) -> object:
    """Bind ``value`` as the active request id.

    Returns the token so the caller can ``reset`` it later — important inside
    middleware so concurrent requests don't bleed ids into each other.
    """
    return _request_id_var.set(value)


def reset_request_id(token: object) -> None:
    _request_id_var.reset(token)  # type: ignore[arg-type]


class JsonFormatter(_BaseJsonFormatter):
    """JSON formatter that always emits ``request_id`` (``"-"`` if unset)."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record.setdefault("level", record.levelname)
        log_record.setdefault("logger", record.name)
        log_record["request_id"] = get_request_id() or "-"
