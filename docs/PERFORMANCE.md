# PERFORMANCE — notify-relay

Performance goals, capacity math, and where to look when tuning.

## SLO (Service Level Objectives)

| Metric | Target |
|---|---|
| `POST /messages` P95 latency | < 150 ms (Postgres insert + enqueue, no external IO) |
| `POST /messages` success rate (2xx or expected 4xx) | ≥ 99.9% over 28 days |
| Email delivery P95 (queue → SMTP accept) | < 30 s |
| Webhook delivery P95 (queue → HTTP 2xx) | < 2 s |
| Messages `queued` for longer than 5 min | 0 (outside incidents) |

## Capacity per node

Baseline node: 4 CPU / 8 GB RAM. Services share nodes:

| Pods per node | CPU req | Mem req |
|---|---|---|
| api × 1 (gunicorn 4 workers, sync) | 1.5 | 1 GB |
| worker-high × 1 (concurrency=8) | 0.5 | 0.5 GB |
| worker-default × 1 (concurrency=4) | 0.5 | 0.5 GB |
| worker-low × 1 (concurrency=2) | 0.2 | 0.3 GB |
| beat × 1 | 0.1 | 0.1 GB |

### API throughput

Sync gunicorn under Django + 1 PG insert per request (with overhead):

```
T_request ≈ T_validation + T_render + T_db_insert + T_redis_enqueue
         ≈ 5 ms   + 2 ms   + 8 ms          + 1 ms          ≈ 16 ms
```

With 4 sync workers: 4 × (1000 / 16) ≈ **250 RPS per node**. SLO target 200 RPS → 25% headroom.

Bottlenecks as you scale:
- PG connections: gunicorn 4 workers × `CONN_MAX_AGE` without pgbouncer = 4 connections. Across 5 nodes = 20. PG `max_connections=100` → ceiling around 25 nodes without pgbouncer. **With pgbouncer (transaction pooling) — effectively unlimited** for our load.
- Template render is not cached (a fresh `Template(src)` each time). At 200 RPS with an average template that's ~400 ms of CPU per second per node — **3% of 4 CPU**, leave it.

### Email throughput

A single SMTP send ≈ 150 ms handshake + 50 ms DATA. With concurrency=8 in worker-high and concurrency=4 in worker-default:

```
worker-high:    8 / 0.2 s ≈ 40 email/sec
worker-default: 4 / 0.2 s ≈ 20 email/sec
Σ              ≈ 60 email/sec ≈ 3600 email/min
```

The real bottleneck is the upstream MTA. Most providers (Postmark, SES) cap at ≈ 14 email/sec per account without warm-up. You need either a higher provider limit or parallel credits from several.

### Webhook throughput

A webhook = 1 HTTP POST with a 10s timeout, typically < 200 ms. With `concurrency=4 (worker-default)` ≈ 20 webhook/sec ≈ 1200/min.

If recipients are slow (P95 > 1s), concurrency doesn't help; raise `--concurrency` proportionally. Carefully — each concurrent worker means a PG connection (see ceiling above).

## DB load profile

| Query | Frequency | Cost | Index |
|---|---|---|---|
| `INSERT INTO messages` | ~ RPS | ~5 ms | PK (ULID), unique `(api_key_id, idempotency_key)` |
| `SELECT ... WHERE api_key_id=? AND idempotency_key=?` (idempotency lookup) | ~ RPS | ~1 ms | unique idx (above) |
| `SELECT ... WHERE status='queued' AND scheduled_at<=now()` (Beat) | 1/min | depends on queue_depth | `message_status_sched_idx` (status, scheduled_at) |
| `SELECT ... WHERE created_at < now() - interval '90 days'` (cleanup) | 1/day | index-driven, not a full scan | `message_created_idx` |

### What `EXPLAIN ANALYZE` should show

Idempotency lookup on hot data:
```
Index Scan using messages_message_idempotency_unique on messages
  Index Cond: ((api_key_id = $1) AND (idempotency_key = $2))
Planning Time: 0.1 ms
Execution Time: < 1 ms
```

If you see `Seq Scan` instead of `Index Scan`, the index isn't being used. Check statistics (`ANALYZE messages`) and parameter types.

Beat dispatch:
```
Index Scan using message_status_sched_idx on messages
  Index Cond: ((status = 'queued') AND (scheduled_at <= now()))
  Rows Removed by Filter: 0
Limit  (cost=... rows=200)
```

At large `queue_depth` (> 100k) add a partial index:
```sql
CREATE INDEX message_pending_idx
ON messages (scheduled_at)
WHERE status = 'queued';
```

## Micro-benchmarks

Local `pytest-benchmark` on critical paths (planned next iteration):

```
bench_hmac_signature:        ~5 µs/op        (negligible)
bench_template_render:       ~80 µs/op       (caching uncritical)
bench_argon2_verify:         ~25 ms/op       (intentionally slow, 4 rounds)
bench_idempotency_lookup:    ~700 µs/op      (PG over loopback)
```

`bench_argon2_verify` is the most expensive operation, so it is not on the API's hot path (the rate-limit middleware uses `HASH(api_key)[:16]` for bucket identity, **not** an argon2 verify). Argon2 verify runs only in DRF auth, which DRF caches per request.

## Load test (planned)

`locust` or `k6`. Bench:

- 1 node (api + worker-default), Postgres / Redis on the same machine.
- 1k pre-seeded `ApiKey`, 10 pre-seeded `TemplateVersion`.
- Happy-path scenario: 80% `POST /messages` with email channel, 20% webhook against an echo server (`https://httpbin.org/status/200`).

Target numbers:
- P95 `POST` < 150 ms at 200 RPS — **green**
- P95 `POST` < 500 ms at 400 RPS — **grey, degradation point**
- Stable error rate < 0.1% — **green**

Run results will land in [`docs/LOAD_TEST_RESULTS.md`](LOAD_TEST_RESULTS.md) (after the load-test tooling is built).

## Tuning checklist

In order of increasing effort:

1. **+pgbouncer** in front of PG — removes the connection ceiling.
2. **Materialized View** for analytics (message counts by `status` over a period) — not for the hot path, but for admin dashboards.
3. **TemplateVersion cache** — read-mostly, rarely changes. `lru_cache(1024)` keyed by `(name, version)`.
4. **Async Django views** for `POST /messages` (Django 5 ASGI + asyncpg) — but that requires rewriting middleware and services as async. Visible only above 500 RPS/node. **Don't do this prematurely.**
5. **Partition `messages`** by `created_at` (monthly) — when the table exceeds 100 GB.
