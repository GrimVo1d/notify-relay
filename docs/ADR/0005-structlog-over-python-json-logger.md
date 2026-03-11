# ADR-0005: Structured logging via structlog

**Status:** Accepted (supersedes the earlier `python-json-logger` choice)
**Date:** 2026-03-05

## Context

Originally JSON logging was done through `python-json-logger.JsonFormatter` — we subclassed it, and in `add_fields` we added `level`, `logger`, and `request_id` from a `ContextVar`. That worked for the basic "one request_id" case, but as soon as you stepped outside a web request scope (a Celery task, a background process) it became awkward — every new context needed its own ContextVar threaded through by hand.

Maintenance comparison: `python-json-logger` has ~600k downloads/day and is maintained, but is effectively frozen on minimum viable features. `structlog` has 6M/day, active development, and in 2024+ is the de facto standard for structured logging in Python outside Django.

## Decision

Switch to `structlog` (see commit `refactor(core): swap python-json-logger for structlog with bound contextvars`). Specifically:

1. **Pipeline.** A single `ProcessorFormatter` for stdlib loggers and `structlog.get_logger()` — both go through the same processors (timestamp, level, request_id, exc_info, JSON-render).
2. **Context.** `structlog.contextvars.bind_contextvars(request_id=..., user_id=...)` instead of hand-rolled ContextVars. Any field is bound in one line, lands in EVERY record in that context, and is just as easy to reset via tokens.
3. **Two renderers.** `JSONRenderer()` for prod, `ConsoleRenderer(colors=False)` for dev. Switched via the `LOG_FORMAT` env var.

## Consequences

**+** Unified pipeline: the same log output for `logging.getLogger`, `structlog.get_logger`, and `print → captured by logger` paths. Previously the JsonFormatter only applied to stdlib logs.
**+** Free structured fields: `log.info("delivery.attempt", message_id=..., channel=..., result=...)` produces a JSON object with those keys directly. No `extra={...}` or string concatenation.
**+** Better dev UX: ConsoleRenderer prints fields flat key=value with colors, readable without `jq`.
**−** A touch more boilerplate in `apps/core/logging.py` (one `configure_structlog()` call from `AppConfig.ready()`).
**−** New contributors have to know both APIs: `logging.getLogger` (for existing code) and `structlog.get_logger` (preferred in new modules). In practice they don't get confused — both write to the same stream.

## Alternatives

1. **Stay on `python-json-logger`.** Works, but requires bespoke ContextVar plumbing for each case. Became uncomfortable as soon as the first Celery task needed its own bound fields.
2. **`loguru`.** More "magical" (auto-rotation, threads-on-import). Doesn't play nicely with the Django LOGGING dict config without contortions. Too many opinionated defaults.
3. **`opentelemetry-sdk` with an OTLP exporter to Loki/Grafana.** Worth doing alongside tracing, but that's a separate step (see roadmap "middle+ extra"). For now we stay on Prometheus metrics + structured stdout logs.

## Migration notes

- Old code that did `set_request_id(value) → token` / `reset_request_id(token)` still works — the API is preserved, only the implementation now uses `structlog.contextvars.bind/reset_contextvars`.
- New code can write `structlog.get_logger(__name__).info("event_name", **fields)` directly and get JSON automatically.

## Related

- [ADR-0001](0001-idempotency-storage.md) — same "single source of truth" approach
- Commit `refactor(core): swap python-json-logger for structlog with bound contextvars`
- [`src/apps/core/logging.py`](../../src/apps/core/logging.py)
