# Architecture

## Components

```
                ┌────────────────────────── api (gunicorn, Django + DRF) ──────────────────────────┐
service ──HTTP──▶ probes → request id → rate limit → auth → views → services ──▶ PostgreSQL       │
                └───────────────────────────────────────│───────────────────────────────────────────┘
                                                        │ on commit: send_email / send_webhook
                                                        ▼
                         Redis ── queues high / default / low ──▶ worker-high / worker-default / worker-low
                           ▲                                          │  render snapshot → channel.send()
                           │ dispatch_scheduled (every 60 s)          ▼
                         beat ── cleanup_old_messages (03:00)     SMTP server / webhook receiver
```

| Process | Role |
|---|---|
| `api` | REST API, Django admin, OpenAPI, health probes, `/metrics` with database-derived gauges |
| `worker-<queue>` | Celery prefork workers, one deployment per priority queue; serve delivery metrics on `CELERY_METRICS_PORT` |
| `beat` | Single instance. Every minute releases due scheduled messages and re-enqueues lost ones; at 03:00 UTC deletes finished messages older than 90 days |
| PostgreSQL | Messages, attempts, dead letters, templates, API keys; the source of truth |
| Redis | Celery broker and result backend (db 1 and 2), rate-limit buckets (db 0) |

## Message lifecycle

```
            POST /messages
                 │
                 ▼
             ┌────────┐  worker picks up  ┌─────────┐  2xx / 250   ┌──────┐
             │ queued │ ────────────────▶ │ sending │ ───────────▶ │ sent │
             └────────┘                   └─────────┘              └──────┘
                 ▲   transient failure, retries left │  │ permanent failure
                 └───────────────────────────────────┘  ▼
                                    ┌──────────────────────────────┐
                                    │ failed (no retry) / dead     │──▶ dead_letter row
                                    │ (retries exhausted)          │
                                    └──────────────────────────────┘
```

1. **Accept** (`apps/messages_api/services.py`). The API looks up
   `(api_key, Idempotency-Key)`. An existing row with the same payload is returned as is; a
   different payload is a `409`. A concurrent duplicate that loses the insert race on the
   unique constraint is answered the same way. Otherwise the template is resolved (latest or
   pinned version) and rendered immediately. The rendered subject and body are stored on the message, so later
   template edits never change a queued message, and render errors surface as `422` to the
   caller rather than in a worker.
2. **Enqueue.** Messages without a future `scheduled_at` get `enqueued_at = now` and their task
   is published after the transaction commits, to the queue named by `priority`. If the
   broker is unreachable the caller still gets `202`; the message is picked up as stranded.
   Scheduled messages keep `enqueued_at = NULL` until Beat's `dispatch_scheduled` releases them.
   The same job re-enqueues `queued` messages whose `enqueued_at` is older than 15 minutes
   (a task lost by the broker). Rows are claimed with `SELECT … FOR UPDATE SKIP LOCKED`, so
   several Beat runs can't double-publish.
3. **Deliver** (`tasks/delivery.py`). The task locks the row, skips it if already finished,
   claims it (`sending`, with its task id in `claimed_by`) and calls the channel. A second task
   for the same message, e.g. from the stranded re-enqueue, finds it claimed and stops; the
   owner's retries keep the task id and continue. Every call is recorded as a `DeliveryAttempt`
   with the SMTP code or HTTP status.
4. **Retry or give up.** Channels return a `ChannelResult` with `transient` set for errors
   worth retrying. Transient errors retry via Celery `autoretry_for` with exponential backoff
   from 30 s, capped at 1 h, with jitter, up to 6 retries. Then the message becomes `dead`.
   Permanent errors make it `failed` at once. Both write a `DeadLetter` row with a payload
   snapshot.

Tasks use `acks_late` and `prefetch_multiplier = 1`, so a worker crash returns the task to
the queue: delivery is **at least once**. Webhook receivers should de-duplicate on
`X-Notify-Message-Id`.

## Channels

Both implement `send(message) -> ChannelResult` (`apps/channels/`, see ADR-0003).

| Channel | Success | Transient | Permanent |
|---|---|---|---|
| Email (Django SMTP backend, body sent as `text/html`) | accepted by the server | SMTP 4xx, connection errors | SMTP 5xx |
| Webhook (httpx) | 2xx | 408, 425, 429, 5xx, timeouts, connection errors | other statuses, including 3xx; a URL that no longer passes the SSRF guard |

The webhook POST carries the rendered template body as `application/json`; a webhook template
must render to valid JSON, which is checked when the message is created. See
[GETTING_STARTED](GETTING_STARTED.md#6-receive-webhooks) for the headers and verification.

## Templates

Templates are named, belong to one channel and have immutable, numbered versions
(`apps/templating/`). They render in a Jinja2 `SandboxedEnvironment`, which blocks
attribute escapes such as `__class__`. Missing variables render empty
(`ChainableUndefined`). Email bodies are autoescaped (HTML); subjects and webhook bodies are
not, and a webhook body must be valid JSON (`{{ value | tojson }}` quotes context values). Template bodies are capped at 65,536 characters and rendered output at 262,144; any
exception during rendering becomes a `RenderError` and a `422`.

## Security model

| Concern | Mechanism | Code |
|---|---|---|
| Service authentication | `X-API-Key: nr_<key_id>_<secret>`. `key_id` (48 random bits) is a unique lookup column; the 192-bit secret is stored as `HMAC-SHA256(API_KEY_HASH_PEPPER, secret)` and compared in constant time. One indexed row per request. | `apps/core/security.py`, `authentication.py` |
| Staff authentication | Django users with `is_staff`: JWT (simplejwt, HS256, 15 min access) for the API, session for admin and OpenAPI UI | `settings/base.py` |
| Authorization | Messages: sent by API keys only, read back only by the owning key (staff see all). Templates and API keys: staff only. | `apps/*/views.py` |
| Rate limiting | Redis token bucket per verified key (`rl:key:<pk>`, the key's own limits), per client IP for everything else under `/api/v1/` (`rl:ip:<addr>`, IPv6 by /64), and a strict per-IP bucket for `/api/v1/auth/` and `/admin/login/` (`rl:auth-ip:<addr>`). Redis time is used inside the Lua script. If Redis is down the limiter fails open. | `apps/core/middleware.py`, `apps/ratelimit/` (ADR-0002) |
| SSRF | Webhook URLs must be `http(s)` and resolve only to globally routable, non-multicast addresses; IPv6 transition and legacy ranges (NAT64, 6to4, Teredo, IPv4-compatible, SIIT, site-local) and `WEBHOOK_BLOCKED_NETWORKS` are refused too. Checked at creation (best effort: a DNS answer slower than 2 s defers the check) and again at send. Host names are handled as IDNA 2008 A-labels throughout. The worker connects to the vetted IP, keeps `Host` and TLS SNI, ignores proxy variables, never follows redirects and never reads the response body. | `apps/messages_api/validators.py`, `apps/channels/webhook.py` |
| Webhook authenticity | `X-Notify-Signature` over `"<timestamp>.<body>"` with the sending key's own `webhook_secret` | `apps/channels/security.py` |
| Configuration | The production profile refuses to start with empty, short (< 32 chars) or `dev-`/`ci-`/`test-` secrets, or with `ALLOWED_HOSTS` empty or `*`; it enables HTTPS redirect, HSTS and secure cookies. | `settings/prod.py` |

Threats and residual risks are in [THREAT_MODEL](THREAT_MODEL.md).

## Observability

- **Logs.** structlog over stdlib logging: JSON (`LOG_FORMAT=json`) or console. API requests
  carry a request id from `X-Request-ID` (or a new ULID), echoed in the response.
- **Probes.** `/health/live` (process up) and `/health/ready` (database and Redis reachable,
  `503` otherwise) are answered by the first middleware, before host validation, HTTPS redirect
  and rate limiting, so orchestrators can call them by pod IP. Failure details go to the log only.
- **Metrics.** Delivery counters and the duration histogram are produced in the workers; each
  worker container aggregates its prefork children (Prometheus multiprocess mode) and serves
  them on `CELERY_METRICS_PORT`. Queue depth and dead-letter size are read from the database
  when the API's `/metrics` is scraped. The metric list is in [DEPLOYMENT](DEPLOYMENT.md#metrics);
  `docs/grafana-dashboard.json` is a ready-made dashboard.

## Code layout

```
manage.py                        puts src/ on sys.path, defaults to settings.dev
src/notify_relay/
    settings/{base,dev,prod,test}.py
    urls.py, wsgi.py, asgi.py
    celery.py                    Celery app, task modules, worker metrics server
src/apps/
    core/                        ApiKey model and admin API, X-API-Key auth, key hashing,
                                 request-id and rate-limit middleware, probes, metrics, logging
    templating/                  Template / TemplateVersion, sandboxed renderer, template API
    messages_api/                Message / DeliveryAttempt / DeadLetter, create service,
                                 recipient and SSRF validation, message API
    channels/                    EmailChannel, WebhookChannel, webhook signing
    ratelimit/                   TokenBucket and its Lua script
src/tasks/
    delivery.py                  send_email / send_webhook, retry and dead-letter logic
    scheduler.py                 dispatch_scheduled, cleanup_old_messages (Beat jobs)
tests/unit, tests/integration    in-process: SQLite, eager Celery, fakeredis, locmem mail
docker/                          Dockerfile and entrypoint (api | worker <queue> | beat | migrate | shell)
docs/                            this documentation, ADRs, Grafana dashboard
```

The settings profiles: `dev` (debug, throwaway secrets, local hosts), `test` (in-process
stack, used by pytest), `prod` (strict, the image default).
