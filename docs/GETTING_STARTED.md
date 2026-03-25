# Getting started

Everything runs in Docker; the host needs only Docker with the Compose plugin, `make`, `curl`
and `python3` (used below to pick a field out of JSON).

## 1. Start the stack

```bash
cp .env.example .env            # empty secrets fall back to throwaway dev values
docker compose up -d --build
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
```

| Service | Address (host) | Purpose |
|---|---|---|
| `api` | http://localhost:8000 | REST API, admin, OpenAPI, probes, `/metrics` |
| `worker-high`, `worker-default`, `worker-low` | — | Celery workers, one per priority queue; metrics on :9100 inside the network |
| `beat` | — | Releases scheduled messages, re-enqueues lost ones, nightly cleanup |
| `db` | 127.0.0.1:5432 | PostgreSQL 16 |
| `redis` | 127.0.0.1:6379 | Celery broker and results, rate-limit buckets |
| `mailpit` | http://localhost:8025 | Catches all outgoing mail |

All host ports are bound to 127.0.0.1. `make up`, `make down`, `make logs` and `make ps` wrap
the usual Compose commands.

## 2. Get a staff token

Templates and API keys are managed by staff users with a JWT:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "<user>", "password": "<password>"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["access"])')
```

Access tokens live 15 minutes; `POST /api/v1/auth/token/refresh/` exchanges the refresh token
for a new one.

## 3. Issue an API key for a service

```bash
curl -s -X POST http://localhost:8000/api/v1/api-keys/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "billing", "rate_limit_per_min": 100, "burst": 200}'
```

The response contains `key` (`nr_<key_id>_<secret>`) and `webhook_secret`. Both are shown
**only once**; store them in the calling service. `POST /api/v1/api-keys/{id}/revoke/` disables
a key.

## 4. Create a template

```bash
curl -s -X POST http://localhost:8000/api/v1/templates/ \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "welcome", "channel": "email",
       "subject_template": "Welcome, {{ name }}",
       "body_template": "<p>Hello {{ name }}!</p>"}'
```

Posting the same name again appends version 2, 3, …; messages use the latest version unless
they pin `template_version`. A name belongs to one channel: reusing it for the other channel
returns `409`.

## 5. Send a message

Only API keys can send. Every request needs a unique `Idempotency-Key`:

```bash
curl -s -X POST http://localhost:8000/api/v1/messages/ \
  -H "X-API-Key: $NOTIFY_KEY" -H 'Idempotency-Key: signup-42' \
  -H 'Content-Type: application/json' \
  -d '{"channel": "email", "recipient": "alice@example.internal",
       "template": "welcome", "context": {"name": "Alice"}, "priority": "high"}'
```

The answer is `202 Accepted` with the message, `"status": "queued"`. Poll
`GET /api/v1/messages/{id}/` until it becomes `sent`, and open Mailpit to see the mail.

Optional fields: `template_version` (pin a version), `priority` (`high` / `default` / `low`),
`scheduled_at` (ISO 8601; the message waits until then, released by Beat within a minute).

Errors carry a `code` and a `detail` (`validation_error` carries per-field `errors` instead):

| Status | `code` | Cause |
|---|---|---|
| 400 | `idempotency_key_missing`, `idempotency_key_too_long` | Header absent or longer than 128 characters |
| 401 | — | Missing, malformed, unknown or revoked API key |
| 403 | `api_key_required` | A staff user (JWT or session) tried to send |
| 409 | `idempotency_conflict` | Same `Idempotency-Key`, different payload |
| 422 | `validation_error` | Bad body, invalid recipient, blocked webhook URL |
| 422 | `template_not_found`, `template_render_failed` | Unknown template or version, template failed to render |
| 429 | `rate_limited` | Bucket empty; wait `Retry-After` seconds |

## 6. Receive webhooks

For `"channel": "webhook"` the recipient is an `http(s)` URL that must resolve to a globally
routable address (localhost, private, CGNAT and similar ranges are refused). Internationalised
host names are resolved and sent in their IDNA 2008 form. The relay POSTs a
compact JSON envelope:

```json
{"data": {"order_id": 42}, "message_id": "01J…", "template": "order-paid", "timestamp": "2026-…"}
```

with these headers:

| Header | Value |
|---|---|
| `X-Notify-Message-Id` | Message id; de-duplicate on it, deliveries are at-least-once |
| `X-Notify-Timestamp` | Unix seconds at send time |
| `X-Notify-Signature` | `sha256=` + hex `HMAC-SHA256(webhook_secret, "<timestamp>.<raw body>")` |

Any 2xx is success. 408, 425, 429 and 5xx are retried; other statuses, including redirects,
are permanent failures. The response body is ignored and not read. Verifying on the receiving side:

```python
import hashlib, hmac, time


def verify(raw_body: bytes, headers: dict[str, str], secret: str, tolerance_s: int = 300) -> bool:
    ts = headers.get("X-Notify-Timestamp", "")
    signature = headers.get("X-Notify-Signature", "")
    if not (ts.isascii() and ts.isdigit()) or abs(time.time() - int(ts)) > tolerance_s:
        return False
    expected = (
        "sha256="
        + hmac.new(secret.encode(), ts.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected.encode(), signature.encode())
```

## 7. Development workflow

Tooling runs in a `python:3.12-slim` container with the virtualenv kept in the
`notify-relay-venv` volume:

```bash
make dev-setup     # once, and after the lock files change
make test          # pytest: SQLite in memory, eager Celery, fakeredis, locmem mail
make lint          # ruff check + ruff format --check
make fmt           # ruff format + ruff check --fix
make typecheck     # mypy --strict
make migrations-check
make audit         # pip-audit against requirements.lock
make lock          # re-pin requirements.lock and requirements-dev.lock after editing pyproject.toml
```

Dependencies are declared in `pyproject.toml` and pinned with hashes in `requirements.lock`
(runtime, used by the Docker image) and `requirements-dev.lock` (tests and tooling).

If you have Python 3.12 locally, `pre-commit install` inside a virtualenv with
`requirements-dev.lock` installed enables the same checks as git hooks.
