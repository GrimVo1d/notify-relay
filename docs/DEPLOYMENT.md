# Deployment

One image (`docker/Dockerfile`) runs every process; the entrypoint picks the role:

| Command | Process |
|---|---|
| `api` (default) | gunicorn on :8000 (`GUNICORN_WORKERS`, default 4; `GUNICORN_TIMEOUT`, default 30) |
| `worker <queue>` | Celery worker for `high`, `default` or `low` (`CELERY_CONCURRENCY`, default 4) |
| `beat` | Celery Beat; run exactly **one** |
| `migrate` | `manage.py migrate --noinput` |
| `shell` | Django shell |
| anything else | executed as is, e.g. `python manage.py createsuperuser` |

The image defaults to `DJANGO_SETTINGS_MODULE=notify_relay.settings.prod`, runs as an
unprivileged user on read-only code, and installs dependencies from the hash-pinned
`requirements.lock`. `docker-compose.yml` is a development stack built on the same image with
the `dev` profile.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `notify_relay.settings.prod` in the image | `dev`, `test` or `prod` |
| `SECRET_KEY` | — | Django secret |
| `JWT_SIGNING_KEY` | — | HS256 key for staff JWTs |
| `API_KEY_HASH_PEPPER` | — | Key for API-key hashes. Changing it invalidates every key |
| `WEBHOOK_HMAC_SECRET` | — | Webhook signing secret for API keys without their own (keys created via the API always have one) |
| `ALLOWED_HOSTS` | empty | Comma-separated host names |
| `DEBUG` | `0` (`1` in `dev`) | Forced off in `prod` |
| `DATABASE_URL` | SQLite file | `postgres://user:password@host:5432/db` |
| `REDIS_URL` | `redis://localhost:6379/0` | Rate-limit buckets, readiness check |
| `CELERY_BROKER_URL` | `redis://localhost:6379/1` | |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/2` | |
| `SMTP_HOST`, `SMTP_PORT` | `localhost`, `1025` | |
| `SMTP_USER`, `SMTP_PASSWORD` | empty | |
| `SMTP_TLS` | `0` | STARTTLS |
| `DEFAULT_FROM_EMAIL` | `no-reply@notify-relay.internal` | `From:` of outgoing mail |
| `WEBHOOK_TIMEOUT_S` | `10` | Per-request timeout for webhook POSTs |
| `WEBHOOK_BLOCKED_NETWORKS` | empty | Extra CIDRs to refuse; non-global addresses are always refused |
| `RATE_LIMIT_ENABLED` | `1` | |
| `RATE_LIMIT_DEFAULT_PER_MIN`, `RATE_LIMIT_DEFAULT_BURST` | `100`, `200` | Per-IP limits for requests without a valid API key |
| `RATE_LIMIT_AUTH_PER_MIN`, `RATE_LIMIT_AUTH_BURST` | `10`, `10` | Per-IP limits on `/api/v1/auth/` and `/admin/login/` |
| `NUM_PROXIES` | `0` | Trusted reverse proxies in front of the API; per-IP limits then read `X-Forwarded-For` |
| `SECURE_SSL_REDIRECT` | `1` | `prod` only; set `0` if TLS terminates elsewhere and redirects are done there |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `plain` | `json` or `plain`; `prod` always logs JSON |
| `CELERY_METRICS_PORT` | unset | Workers: serve Prometheus metrics on this port |
| `PROMETHEUS_MULTIPROC_DIR` | unset | Workers: scratch directory for multiprocess metrics; emptied at start |
| `CELERY_BEAT_SCHEDULE_FILE` | `/tmp/celerybeat-schedule` | Beat state file |
| `CELERY_BEAT_SCHEDULER` | `celery.beat:PersistentScheduler` | Beat scheduler class |
| `CELERY_LOG_LEVEL` | `INFO` | Workers and Beat |

Per-key rate limits (`rate_limit_per_min`, `burst`) are set when the key is created.

## Production checklist

- [ ] Generate every secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
      The `prod` profile refuses to start with empty, short or `dev-`/`ci-`/`test-` values.
- [ ] `ALLOWED_HOSTS` lists the real host names (the probes don't need it).
- [ ] TLS terminates at a proxy that sets `X-Forwarded-Proto`; `NUM_PROXIES` matches the
      number of proxies that append to `X-Forwarded-For`. The API port is reachable only
      through that proxy: both headers are trusted as they arrive. Entries must be bare
      addresses (no `:port`), otherwise every client falls back to the proxy's bucket.
- [ ] `python manage.py migrate` runs before the new `api` and workers start.
- [ ] Exactly one `beat`; workers for all three queues.
- [ ] Probes: liveness `GET /health/live`, readiness `GET /health/ready` (both plain HTTP,
      any `Host`). Keep `/health/*` off the public ingress.
- [ ] `/metrics` (API) and the workers' metrics port are reachable only from the monitoring
      network; they are not authenticated. The API's `/metrics` checks `Host`, so the name
      Prometheus scrapes (e.g. `api`) must be in `ALLOWED_HOSTS`.
- [ ] PostgreSQL backups. Redis needs no persistence for correctness: lost tasks are
      re-enqueued by Beat after 15 minutes, buckets simply refill.
- [ ] Request body size limited at the proxy (64 KiB is plenty for `POST /messages`).
- [ ] Optional: the Django admin needs its static files collected and served
      (`collectstatic` into `STATIC_ROOT`); the API and OpenAPI UI work without them.

Smoke-test a built image's production settings without a database:

```bash
docker run --rm -e ALLOWED_HOSTS=relay.internal \
  -e SECRET_KEY=... -e JWT_SIGNING_KEY=... -e API_KEY_HASH_PEPPER=... -e WEBHOOK_HMAC_SECRET=... \
  notify-relay:dev python manage.py check --deploy --fail-level WARNING
```

## Upgrading from API keys with the `nr_live_` prefix

Migration `core.0002` replaces argon2-hashed keys with the `nr_<key_id>_<secret>` format.
Old keys can't be converted: the migration revokes them, keeping the rows for history.
Issue new keys (`POST /api/v1/api-keys/`) and hand them to the services before upgrading.

## Metrics

| Metric | Type | Served by |
|---|---|---|
| `notify_relay_messages_total{channel,status}` | counter: messages reaching `sent`, `failed` or `dead` | workers |
| `notify_relay_delivery_attempts_total{channel,result}` | counter: `success`, `transient_error`, `permanent_error` | workers |
| `notify_relay_delivery_duration_seconds{channel}` | histogram: time in `channel.send()` | workers |
| `notify_relay_queue_depth{channel}` | gauge: messages in `queued` | API `/metrics` |
| `notify_relay_dlq_size` | gauge: rows in the dead-letter table | API `/metrics` |

Scrape the API and every worker container. Import `docs/grafana-dashboard.json` into Grafana
and pick the Prometheus data source. Useful alerts:

- `/health/ready` failing: the API can't reach PostgreSQL or Redis; the log has the cause.
- `notify_relay_queue_depth` growing for several minutes: workers are too few or a channel is
  slow; compare channels and look at `delivery_attempts_total{result="transient_error"}`.
- `increase(notify_relay_dlq_size[1h]) > 0`: messages are failing permanently or exhausting
  retries; read `reason` in the dead-letter table.

## Operations

**Why did a message fail?** Every attempt is a row in `messages_api_deliveryattempt`; failed
and dead messages have a row in `messages_api_deadletter`:

```sql
SELECT m.id, m.channel, m.recipient, d.reason, d.created_at
FROM messages_api_deadletter d JOIN messages_api_message m ON m.id = d.message_id
WHERE d.created_at > now() - interval '1 hour'
ORDER BY d.created_at DESC;
```

**Re-send dead-lettered messages** once the cause is fixed. Clearing `enqueued_at` and setting
`scheduled_at` hands them to the next Beat run:

```sql
BEGIN;
UPDATE messages_api_message
SET status = 'queued', enqueued_at = NULL, scheduled_at = now(), updated_at = now()
WHERE id IN (SELECT message_id FROM messages_api_deadletter WHERE created_at > now() - interval '1 hour');
DELETE FROM messages_api_deadletter WHERE created_at > now() - interval '1 hour';
COMMIT;
```

**Broker outage.** Messages accepted while Redis is down still get `202`; publishing the task
fails, is logged as a warning, and Beat re-enqueues them once `enqueued_at` is 15 minutes old.

**Release scheduled messages right away:**
`docker compose exec beat celery -A notify_relay call tasks.scheduler.dispatch_scheduled`.

**Rotate a service's key:** create a new key, switch the service, then
`POST /api/v1/api-keys/{id}/revoke/` on the old one. The new key comes with a new webhook
secret; receivers must accept both during the switch.

**Retention:** `cleanup_old_messages` deletes `sent`, `failed` and `dead` messages older than
90 days (with their attempts and dead letters) every night at 03:00 UTC.
