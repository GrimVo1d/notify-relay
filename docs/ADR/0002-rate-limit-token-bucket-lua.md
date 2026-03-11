# ADR-0002: Rate-limit — Redis token bucket via atomic Lua

**Status:** Accepted
**Date:** 2026-02-28

## Context

We need to limit request rate per API key: 100 req/min default + burst 200, individually overridable. Several API instances sit behind a load balancer, so the state has to be shared. Two decisions: the algorithm (fixed window / sliding window / token bucket / leaky bucket) and the storage (in-memory with gossip / Redis / DB).

## Decision

**Algorithm:** token bucket. **Storage:** Redis. **Atomicity:** a single Lua script `token_bucket.lua` that reads the counter, refills based on elapsed time, checks and decrements tokens — all in one Redis-side operation.

Key format: `rl:<sha256(api_key_full)[:16]>` for authenticated requests, `rl:ip:<remote_addr>` for anonymous ones (for example on `/api/v1/auth/token/`, although those endpoints are currently excluded from the limit and have their own protection).

## Consequences

**+** Burst-friendly: short spikes are allowed, sustained ones are not. That matches what service-to-service clients want (not "even RPS" but batch waves).
**+** Lua atomicity means no TOCTOU between read and decrement. Stays correct at > 1000 RPS without oversubscription.
**+** State survives worker restarts and pod reschedules. `EXPIRE` on the key cleans up garbage.
**−** The Lua script must be tested separately (see `tests/unit/test_token_bucket.py` via `fakeredis[lua]`).
**−** Redis becomes a hard dependency on the API's critical path. When Redis is down, the middleware can either fail-open (risk of overload) or fail-closed (risk of false 429s). The code fails open and logs, because the rate limiter is downstream-protection, not a security control.

## Alternatives

1. **Sliding window log.** More precise, but stores timestamps for every event in the window — O(N) memory and traffic. For a burst of 200 that's overkill.
2. **Fixed window counter.** Simplest, but yields a "doubled peak" at window boundaries (199 in the last second of a minute + 199 in the first second of the next).
3. **Leaky bucket.** Equivalent in effect to token bucket, but less intuitive to configure (rate + capacity vs rate + burst — the same thing in different terms).
4. **DRF Throttle (per-view).** Stored in the Django cache. Works on a single node; multi-node needs `django-redis-cache`. Not atomic — there's a race between read and increment. The DRF throttle classes themselves are illustrative, not production-grade.
5. **`django-ratelimit` + Redis.** Looks similar from the outside; the same race conditions without Lua inside.
6. **In-memory + gossip.** Too complex for one service, overkill.

## Related

- Lua source: [`src/apps/ratelimit/lua/token_bucket.lua`](../../src/apps/ratelimit/lua/token_bucket.lua)
- Tests with fakeredis[lua]: [`tests/integration/test_e2e.py::test_rate_limit_returns_429_with_retry_after`](../../tests/integration/test_e2e.py)
