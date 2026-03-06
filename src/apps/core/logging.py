"""Structured logging with structlog.

* All loggers (`logging.getLogger(...)`) AND structlog (`structlog.get_logger(...)`)
  share the same output pipeline via :class:`structlog.stdlib.ProcessorFormatter`.
* The active request id is held in a structlog contextvar so it survives
  ``await`` boundaries and Celery task contexts; both web requests and
  worker tasks can :func:`set_request_id` / :func:`reset_request_id`.
* JSON output goes to ``LOG_FORMAT=json`` (prod). Plain console renderer
  goes to ``LOG_FORMAT=plain`` (dev).
"""

from __future__ import annotations

from typing import Any

import structlog

_REQUEST_ID_KEY = "request_id"

_PRE_CHAIN = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_structlog() -> None:
    """Bind structlog to stdlib logging. Idempotent — safe to call repeatedly."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


class JsonFormatter(structlog.stdlib.ProcessorFormatter):
    """Plug-in for Django's LOGGING dict — emits one JSON object per record."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("processor", structlog.processors.JSONRenderer())
        kwargs.setdefault("foreign_pre_chain", _PRE_CHAIN)
        super().__init__(*args, **kwargs)


class PlainFormatter(structlog.stdlib.ProcessorFormatter):
    """Human-readable console output for local dev."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("processor", structlog.dev.ConsoleRenderer(colors=False))
        kwargs.setdefault("foreign_pre_chain", _PRE_CHAIN)
        super().__init__(*args, **kwargs)


def get_request_id() -> str | None:
    return structlog.contextvars.get_contextvars().get(_REQUEST_ID_KEY)


def set_request_id(value: str) -> dict[str, Any]:
    """Bind the request id; returns tokens that should be passed to :func:`reset_request_id`."""
    return structlog.contextvars.bind_contextvars(**{_REQUEST_ID_KEY: value})


def reset_request_id(tokens: dict[str, Any]) -> None:
    structlog.contextvars.reset_contextvars(**tokens)
