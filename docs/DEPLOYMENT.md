# DEPLOYMENT — notify-relay

## Environment variables

| Name | Type | Default | Description |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | str | `notify_relay.settings.dev` | Settings profile: `dev` / `test` / `prod` |
| `SECRET_KEY` | str | `dev-insecure-change-me` | Django secret. **Must be changed in prod** |
| `DEBUG` | bool | `0` | `1` for dev only |
| `ALLOWED_HOSTS` | csv | `*` | Comma-separated hostnames |
| `DATABASE_URL` | url | `sqlite:///…` | `postgres://user:pass@host:5432/db` |
| `REDIS_URL` | url | `redis://localhost:6379/0` | Shared Redis (rate-limit, cache) |
| `CELERY_BROKER_URL` | url | `redis://localhost:6379/1` | Celery broker |
| `CELERY_RESULT_BACKEND` | url | `redis://localhost:6379/2` | Celery results |
| `SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `TLS` | — | mailpit | SMTP settings |
| `DEFAULT_FROM_EMAIL` | str | `no-reply@notify-relay.local` | `From:` for the email channel |
| `JWT_SIGNING_KEY` | str | `dev-insecure-jwt` | JWT signing key. **Must be changed** |
| `API_KEY_HASH_PEPPER` | str | `dev-insecure-pepper` | Pepper for argon2 API-key hashes. **Change and persist** |
| `WEBHOOK_HMAC_SECRET` | str | `dev-insecure-webhook-secret` | Global secret for outbound webhook HMAC |
| `WEBHOOK_TIMEOUT_S` | int | `10` | Timeout on outbound POST |
| `WEBHOOK_BLOCKED_NETWORKS` | csv | private subnets | CIDR block list — SSRF guard |
| `RATE_LIMIT_ENABLED` | bool | `1` | Global rate-limit switch |
| `RATE_LIMIT_DEFAULT_PER_MIN` | int | `100` | Limit for requests without an API key |
| `RATE_LIMIT_DEFAULT_BURST` | int | `200` | Burst for requests without an API key |
| `LOG_LEVEL` | str | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FORMAT` | str | `plain` | `plain` locally, `json` in prod |

See [`src/notify_relay/settings/base.py`](../src/notify_relay/settings/base.py) and [`.env.example`](../.env.example) for the canonical defaults.

## Production checklist

- [ ] Replace **every** `dev-insecure-*` secret — `SECRET_KEY`, `JWT_SIGNING_KEY`, `API_KEY_HASH_PEPPER`, `WEBHOOK_HMAC_SECRET`.
- [ ] `DEBUG=0`, `ALLOWED_HOSTS` set to a concrete list of domains.
- [ ] `DJANGO_SETTINGS_MODULE=notify_relay.settings.prod`.
- [ ] `LOG_FORMAT=json`, logs shipped centrally.
- [ ] `DATABASE_URL` points at a managed Postgres instance (RDS/CloudSQL); the password is not committed.
- [ ] Migrations applied: `manage.py migrate --check`.
- [ ] Services deployed separately: `api` (gunicorn), `worker-high`, `worker-default`, `worker-low`, `beat`. **`beat` is a single instance** (it is the source of truth for the schedule).
- [ ] The load balancer hits `/health/ready` for the readiness check and `/health/live` for liveness.
- [ ] `/metrics` is reachable only from the internal network (or guarded by basic auth).
- [ ] Postgres backups configured (point-in-time recovery + 14+ days of retention).
- [ ] Redis configured with `maxmemory-policy=allkeys-lru` and persistence (RDB or AOF, depending on durability needs).
- [ ] HSTS, secure cookies, CSRF — enforced by `prod.py` (see the file).

## docker-compose deploy (single host / dev-stage)

```bash
cp .env.example .env  # edit secrets
docker compose pull
docker compose build
docker compose up -d
docker compose exec api python manage.py migrate
```

Services:

| Service | Command | Role |
|---|---|---|
| `api` | `gunicorn notify_relay.wsgi` | HTTP layer, port 8000 |
| `worker-high` | `celery worker -Q high` | OTPs, security-critical |
| `worker-default` | `celery worker -Q default` | business notifications |
| `worker-low` | `celery worker -Q low` | digests, cleanup, metrics |
| `beat` | `celery beat` | scheduler (every minute) |
| `db` | `postgres:16` | data store |
| `redis` | `redis:7` | broker + results + rate-limit |
| `mailpit` | `axllent/mailpit` | dev-only SMTP stand with UI + REST on :8025 |

## Metrics and SLIs

`/metrics` exposes a Prometheus endpoint:

- `notify_relay_messages_total{channel,status}` — counter of terminal transitions (sent / failed / dead).
- `notify_relay_delivery_attempts_total{channel,result}` — counter of attempts (success / transient_error / permanent_error).
- `notify_relay_delivery_duration_seconds{channel}` — histogram of time spent in `channel.send()`.
- `notify_relay_queue_depth{channel}` — gauge: number of messages in `queued` (refreshed by Beat every 30s).
- `notify_relay_dlq_size` — gauge: dead-letter table size.

Suggested SLIs:
- **API success rate**: percentage of `2xx` on `POST /messages`. Target ≥ 99.9%.
- **Delivery P95 latency**: `histogram_quantile(0.95, rate(notify_relay_delivery_duration_seconds_bucket[5m]))`. Target < 30s (email) / < 2s (webhook).
- **Queue saturation**: `notify_relay_queue_depth` rising steadily → not enough workers or a channel is down.
- **DLQ growth rate**: `rate(notify_relay_dlq_size[1h])` > 0 → time to investigate.

## Security

- API keys are stored as `argon2(secret + pepper)`; no plain-text secrets in the DB. Keys cannot be recovered — only reissued.
- JWT tokens: short access (15 min) + refresh (7 days). HS256 signature.
- Webhook delivery: URL validation (private subnets blocked), HMAC-SHA256 body signature, no redirects, 10s timeout.
- Rate-limit: 100/min default + per-key override. Bucket in Redis, atomic Lua.
- All secrets via env. `.env` is in `.gitignore`.

## Troubleshooting

| Symptom | Where to look | What to do |
|---|---|---|
| `/health/ready` returns `503` | the JSON response itself, `checks` field | either `db` or `redis` is failing. Verify `DATABASE_URL` / `REDIS_URL` and port reachability |
| Messages pile up in `queued` | `notify_relay_queue_depth` grows | check that `worker-default` is up and not in a crash loop; `celery -A notify_relay inspect active` |
| `429` on every request | `Retry-After` header | the bucket is exhausted. Check the key's `rate_limit_per_min` in the admin; temporarily set `RATE_LIMIT_ENABLED=0` |
| Every webhook returns `422` | log line `webhook URL resolves to a blocked address range` | the host resolves into a private subnet. Check the DNS answer with `dig`/`host`; allow the recipient's public IP via `WEBHOOK_BLOCKED_NETWORKS` |
| `502/503` from the API | gunicorn logs | likely a crash loop. `docker compose logs api --tail=200`. Often the migrator never ran |
| `transient_error` on email | `delivery_attempts.smtp_code` | SMTP 4xx — temporary issue (greylisting, MTA rate-limit). Wait out the backoff |
| Beat doesn't fire tasks | `worker.*` still consumes `default` tasks fine | check that **exactly one** beat instance is running. Two beats → duplicate tasks and slow processing |
| OpenAPI schema is empty | `/api/schema/` | `drf-spectacular` couldn't find a `serializer_class` on a view. See warnings from `manage.py spectacular --file /tmp/s.yml` |
