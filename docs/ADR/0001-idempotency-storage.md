# ADR-0001: Store Idempotency-Key in the DB, not in Redis

**Status:** Accepted
**Date:** 2026-02-28

## Context

`POST /messages` requires an `Idempotency-Key` header. A retry with the same key within a 24h window must return the same `message.id` without creating a duplicate; a retry with a different payload must return `409 Conflict`. We need to decide where to store the `(api_key, idempotency_key) → message_id` mapping.

## Decision

Store idempotency **inside the main `messages` table** via a unique constraint on `(api_key_id, idempotency_key)`. The retry lookup is `SELECT … WHERE api_key_id = ? AND idempotency_key = ?`, served by the unique index. Cleanup of stale keys is handled by the `cleanup_old_messages` Beat task, which deletes rows where `messages.created_at < now() - 90d` (see [ADR-0004](0004-message-id-ulid.md) for retention).

## Consequences

**+** Single source of truth: on an idempotent retry we return the current status of the message (`sent`/`failed`/`dead`), not just an "id from cache". That matters for clients that retry on network errors.
**+** Idempotency survives Redis flaps and the loss of any auxiliary store.
**+** Transactional atomicity: inserting the `Message` and recording the "idempotency marker" is the same row — there is no inconsistency window.
**−** Every POST pays for an extra index `SELECT`, even on the first request. The cost is a few microseconds on a hot index.
**−** TTL is not "free" (the way it is with Redis `EXPIRE`) — it needs a Beat task. We already have one for message retention, so no new infrastructure.

## Alternatives

1. **Redis `SET` with TTL=24h.** Faster lookup, free expiration. Downside: if Redis flaps, idempotency is lost (= duplicates possible). For transactional notifications that is unacceptable.
2. **Hybrid: Redis as a hot cache + DB as fallback.** Adds complexity (two read paths, invalidation) for little gain: the PG unique index on `(api_key_id, idempotency_key)` is enough at the expected RPS (≤ 200/node).
3. **A separate `idempotency_records` table with an FK to messages.** Extra JOIN on every lookup, no upside.

## Related

- [ADR-0002](0002-rate-limit-token-bucket-lua.md) — by contrast, that's where Redis is the right choice, and it explains why.
