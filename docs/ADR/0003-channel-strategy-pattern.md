# ADR-0003: Delivery channels — Strategy pattern with a single `ChannelResult`

**Status:** Accepted
**Date:** 2026-02-28

## Context

We have two channels: email (SMTP) and webhook (outbound HTTP). Each has its own API, its own error codes, and its own "transient vs permanent" semantics. The retry logic in Celery must look the same regardless of which channel is used.

## Decision

Each channel implements the `Channel` protocol (`apps/channels/base.py`):

```python
class Channel(Protocol):
    def send(self, message: Message) -> ChannelResult: ...

@dataclass(frozen=True)
class ChannelResult:
    success: bool
    transient: bool        # only meaningful when success=False
    error_message: str = ""
    http_status: int | None = None
    smtp_code: int | None = None
```

A channel is the only place that knows about SMTP codes or HTTP statuses. Past that boundary the system only sees `ChannelResult`. Retry logic in `_dispatch()`:

- `success=True` → `status=SENT`, done.
- `success=False, transient=True` → `raise TransientError` → Celery autoretry with exponential backoff.
- `success=False, transient=False` → straight to `dead_letter`, no retries.

## Consequences

**+** Adding a new channel = ~50 lines for a class + registration in `enqueue_for()`. Retry / DLQ / metrics logic stays untouched.
**+** The `transient/permanent` contract is explicit — you can't forget it. SMTP 4xx → transient, 5xx → permanent. HTTP 5xx, 408, 425, 429 → transient, other 4xx → permanent. That's documented in the channel code, not scattered through the task layer.
**+** Channel unit tests are simple `send()` mocks. No need to flip DB statuses by hand.
**−** Channels can't return "partial success" (e.g. 1 of 3 recipients delivered). We don't need that — every message has exactly one recipient.
**−** An extra abstraction layer (`ChannelResult`) would be overkill for one channel. With two, it pays for itself.

## Alternatives

1. **Call SMTP/HTTP directly from the task, no abstraction.** Fine for a prototype. As soon as you add the second channel you copy retry logic and grow divergent error semantics. A year later you refactor it into exactly this Strategy pattern. So we do it up front.
2. **Inheritance instead of Protocol** (`class EmailChannel(BaseChannel)`). Stricter, but structural typing is enough for an interface this small and gives nicer mocks in tests (a `@dataclass` is plenty).
3. **Plugin system via entry-points.** Useful when channels live in external packages. We don't need that — both channels are in the monorepo.

## Related

- Code: [`src/apps/channels/`](../../src/apps/channels/)
- Usage: [`src/tasks/delivery.py::_dispatch`](../../src/tasks/delivery.py)
