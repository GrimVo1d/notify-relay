# notify-relay

Транзакционный сервис рассылок: принимает запросы через REST API, помещает их в Celery-очередь и доставляет получателям по двум каналам — **email (SMTP)** и **outbound webhook (HTTP POST + HMAC)**. Поддерживает приоритеты, ретраи, идемпотентность, шаблоны сообщений с версионированием и rate-limit на отправителя.

## Зачем

Чтобы прикладные сервисы (биллинг, аутентификация, заказы) не тащили внутрь себя SMTP-клиенты и логику ретраев, а отдавали уведомления одному надёжному relay-сервису через HTTP.

## Стек

Python 3.12 · Django 5 · DRF · PostgreSQL 16 · Redis 7 · Celery 5 · Docker · GitHub Actions

## Быстрый старт (docker-compose)

```bash
cp .env.example .env
docker compose up -d
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
# API:        http://localhost:8000/api/v1/
# OpenAPI:    http://localhost:8000/api/schema/swagger-ui/
# MailHog UI: http://localhost:8025/
# Metrics:    http://localhost:8000/metrics
# Liveness:   http://localhost:8000/health/live
# Readiness:  http://localhost:8000/health/ready
```

Создайте API-ключ через админку (`/admin/`) или через REST как staff:

```bash
curl -X POST http://localhost:8000/api/v1/api-keys/ \
     -H 'Content-Type: application/json' \
     -u admin:<pwd> \
     -d '{"name": "billing", "rate_limit_per_min": 100, "burst": 200}'
# В ответе ОДИН РАЗ показывается полный ключ — сохраните.
```

## Отправить уведомление

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

Для webhook-канала:

```bash
curl -X POST http://localhost:8000/api/v1/messages/ \
     -H 'X-API-Key: ...' -H 'Idempotency-Key: ...' \
     -d '{"channel":"webhook","recipient":"https://example.com/hook","template":"event","context":{"id":42}}'
```

## Локальная разработка без Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export DJANGO_SETTINGS_MODULE=notify_relay.settings.dev
python manage.py migrate
python manage.py runserver
# отдельным процессом:
celery -A notify_relay worker -Q default --concurrency 4
celery -A notify_relay beat
```

Тесты:

```bash
pytest -q                       # вся пирамида
pytest -q tests/unit            # быстрые unit
pytest -q tests/integration     # in-process e2e
make lint                       # ruff + black + isort
```

## Документация

- [docs/SPEC.md](docs/SPEC.md) — техническое задание (11 разделов)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — компоненты, потоки данных, очереди, транзакционная модель
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — env-переменные, прод-чеклист, троублшутинг
- [docs/ROADMAP.md](docs/ROADMAP.md) — пошаговая история реализации
- `/api/schema/swagger-ui/` — интерактивный OpenAPI у запущенного инстанса

## Ключевые гарантии

- **Идемпотентность.** Повтор `POST /messages` с тем же `Idempotency-Key` возвращает тот же `id` без дубля в БД; повтор с другим payload'ом → `409`.
- **Ретраи.** Транзиентные ошибки канала → exponential backoff + jitter, до 6 попыток. После исчерпания — запись в `dead_letter`.
- **SSRF-защита.** Webhook URL проверяется на запрещённые подсети (`127/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`) при создании и при отправке.
- **HMAC-подпись.** Все исходящие webhook'и подписываются `X-Notify-Signature: sha256=<hex>`.
- **Rate-limit.** Token-bucket на API-key в Redis (atomic Lua); 429 + `Retry-After` при превышении.

## Лицензия

MIT.
