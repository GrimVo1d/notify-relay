# notify-relay

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

A transactional notification relay. Services call one REST endpoint; the relay renders a
stored template, queues the message in Celery and delivers it by **email (SMTP)** or
**outbound webhook (HTTP POST signed with HMAC-SHA256)**, with retries and a dead-letter table.

```
service ──POST /api/v1/messages──▶ api ──▶ PostgreSQL
            X-API-Key                │
            Idempotency-Key          └──▶ Redis (broker) ──▶ worker-{high,default,low} ──▶ SMTP / webhook
```

## Features

- **Idempotent sends.** `Idempotency-Key` is required; a repeat returns the same message,
  a repeat with a different payload returns `409`.
- **Versioned templates.** Jinja2 in a sandbox; a message can pin a template version.
- **Retries.** Transient failures retry with exponential backoff and jitter (up to 6 retries);
  permanent failures and exhausted retries land in the dead-letter table.
- **Scheduled sends.** `scheduled_at` holds a message until Celery Beat releases it.
- **Priorities.** `high`, `default` and `low` queues, each with its own worker pool.
- **Rate limiting.** Token bucket per API key in Redis (atomic Lua), strict per-IP limit on login.
- **SSRF guard.** Webhook targets must be globally routable; the worker connects to the address
  it vetted, so DNS rebinding can't redirect it.
- **Signed webhooks.** `HMAC-SHA256(timestamp.body)` with a secret per API key.
- **Operations.** Prometheus metrics, liveness and readiness probes, JSON logs with request ids,
  OpenAPI schema.

## Stack

Python 3.12 · Django 5.2 · Django REST framework · Celery 5 · PostgreSQL 16 · Redis 7 ·
Jinja2 · httpx · structlog · Docker Compose

## Quick start

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
```

Then follow [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) to issue an API key, create a
template and send the first message. Mail lands in Mailpit at http://localhost:8025.

## Documentation

| Document | Contents |
|---|---|
| [GETTING_STARTED](docs/GETTING_STARTED.md) | Local stack, first message, webhook verification, development workflow |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Components, message lifecycle, delivery, security model, code layout |
| [DEPLOYMENT](docs/DEPLOYMENT.md) | Configuration reference, production checklist, metrics, operations |
| [THREAT_MODEL](docs/THREAT_MODEL.md) | Threats, mitigations and residual risks |
| [ADR/](docs/ADR/) | Architecture decision records |

A running instance serves interactive API docs at `/api/schema/swagger-ui/` (staff only with
the production settings).
