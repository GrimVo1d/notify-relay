# notify-relay

[![CI](https://github.com/GrimVo1d/notify-relay/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/GrimVo1d/notify-relay/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#license)

Transactional notification service: accepts requests via a REST API, queues them in Celery, and delivers them to recipients over two channels — **email (SMTP)** and **outbound webhook (HTTP POST + HMAC)**. Supports priorities, retries, idempotency, versioned message templates, and per-sender rate limiting.

## Why

So application services (billing, auth, orders) don't have to embed SMTP clients and retry logic themselves — they hand notifications off to one reliable relay over HTTP.

## Stack

Python 3.12 · Django 5 · DRF · PostgreSQL 16 · Redis 7 · Celery 5 · Docker · GitHub Actions

## Quick start (docker-compose)

```bash
cp .env.example .env
docker compose up -d
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
# API:        http://localhost:8000/api/v1/
# OpenAPI:    http://localhost:8000/api/schema/swagger-ui/
# Mailpit UI: http://localhost:8025/
# Metrics:    http://localhost:8000/metrics
# Liveness:   http://localhost:8000/health/live
# Readiness:  http://localhost:8000/health/ready
```

Create an API key via the admin (`/admin/`) or as a staff user via REST:

```bash
curl -X POST http://localhost:8000/api/v1/api-keys/ \
     -H 'Content-Type: application/json' \
     -u admin:<pwd> \
     -d '{"name": "billing", "rate_limit_per_min": 100, "burst": 200}'
# The response shows the full key ONCE — save it.
```

## Send a notification

```bash
curl -X POST http://localhost:8000/api/v1/messages/ \
     -H 'Content-Type: application/json' \
     -H 'X-API-Key: nr_live_<secret>' \
     -H 'Idempotency-Key: 8e1c8b3a-...' \
     -d '{
       "channel": "email",
       "recipient": "alice@example.com",
       "template": "welcome",
       "context": {"name": "Alice"},
       "priority": "default"
     }'
# 202 Accepted: {"id": "01HXYZ...", "status": "queued", ...}
```

For the webhook channel:

```bash
curl -X POST http://localhost:8000/api/v1/messages/ \
     -H 'X-API-Key: ...' -H 'Idempotency-Key: ...' \
     -d '{"channel":"webhook","recipient":"https://example.com/hook","template":"event","context":{"id":42}}'
```

## Local development without Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export DJANGO_SETTINGS_MODULE=notify_relay.settings.dev
python manage.py migrate
python manage.py runserver
# in separate processes:
celery -A notify_relay worker -Q default --concurrency 4
celery -A notify_relay beat
```

Tests:

```bash
pytest -q                       # full pyramid
pytest -q tests/unit            # fast unit
pytest -q tests/integration     # in-process e2e
make lint                       # ruff + black + isort
```

## Documentation

- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — env variables, prod checklist, troubleshooting
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — SLOs, capacity math, tuning
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — STRIDE + security checklist
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — on-call playbook
- [docs/ADR/](docs/ADR/) — Architecture Decision Records (idempotency, rate-limit, channels, id, logging)
- [docs/grafana-dashboard.json](docs/grafana-dashboard.json) — Grafana dashboard backed by Prometheus metrics (import as-is)
- `/api/schema/swagger-ui/` — interactive OpenAPI on a running instance

## Key guarantees

- **Idempotency.** Repeating `POST /messages` with the same `Idempotency-Key` returns the same `id` with no duplicate row; repeating with a different payload returns `409`.
- **Retries.** Transient channel errors trigger exponential backoff + jitter, up to 6 attempts. After exhaustion the message lands in `dead_letter`.
- **SSRF protection.** Webhook URLs are validated against forbidden subnets (`127/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`) at creation and at send time.
- **HMAC signatures.** All outbound webhooks are signed with `X-Notify-Signature: sha256=<hex>`.
- **Rate limiting.** Token bucket per API key in Redis (atomic Lua); 429 + `Retry-After` on overage.

## License

MIT.
