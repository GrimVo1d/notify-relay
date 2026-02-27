# DEPLOYMENT — notify-relay

## Переменные окружения

| Имя | Тип | По умолчанию | Описание |
|---|---|---|---|
| `DJANGO_SETTINGS_MODULE` | str | `notify_relay.settings.dev` | Профиль настроек: `dev` / `test` / `prod` |
| `SECRET_KEY` | str | `dev-insecure-change-me` | Django secret. **Обязательно сменить в проде** |
| `DEBUG` | bool | `0` | `1` только в dev |
| `ALLOWED_HOSTS` | csv | `*` | Список хостов (запятая) |
| `DATABASE_URL` | url | `sqlite:///…` | `postgres://user:pass@host:5432/db` |
| `REDIS_URL` | url | `redis://localhost:6379/0` | Общий Redis (rate-limit, кэш) |
| `CELERY_BROKER_URL` | url | `redis://localhost:6379/1` | Celery broker |
| `CELERY_RESULT_BACKEND` | url | `redis://localhost:6379/2` | Celery results |
| `SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `TLS` | — | mailhog | SMTP-параметры |
| `DEFAULT_FROM_EMAIL` | str | `no-reply@notify-relay.local` | From: для email-канала |
| `JWT_SIGNING_KEY` | str | `dev-insecure-jwt` | Подпись JWT. **Обязательно сменить** |
| `API_KEY_HASH_PEPPER` | str | `dev-insecure-pepper` | Перец для argon2-хеша API-ключей. **Сменить и хранить** |
| `WEBHOOK_HMAC_SECRET` | str | `dev-insecure-webhook-secret` | Глобальный секрет для HMAC-подписи webhook'ов |
| `WEBHOOK_TIMEOUT_S` | int | `10` | Таймаут на исходящий POST |
| `WEBHOOK_BLOCKED_NETWORKS` | csv | приватные подсети | Список CIDR — запрет SSRF |
| `RATE_LIMIT_ENABLED` | bool | `1` | Глобальный выключатель rate-limit |
| `RATE_LIMIT_DEFAULT_PER_MIN` | int | `100` | Лимит для запросов без API-key |
| `RATE_LIMIT_DEFAULT_BURST` | int | `200` | Burst для запросов без API-key |
| `LOG_LEVEL` | str | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FORMAT` | str | `plain` | `plain` локально, `json` в проде |

Подробности дефолтных значений — в [`src/notify_relay/settings/base.py`](../src/notify_relay/settings/base.py) и [`.env.example`](../.env.example).

## Прод-чеклист

- [ ] Подменены **все** `dev-insecure-*` секреты — `SECRET_KEY`, `JWT_SIGNING_KEY`, `API_KEY_HASH_PEPPER`, `WEBHOOK_HMAC_SECRET`.
- [ ] `DEBUG=0`, `ALLOWED_HOSTS` — конкретный список доменов.
- [ ] `DJANGO_SETTINGS_MODULE=notify_relay.settings.prod`.
- [ ] `LOG_FORMAT=json`, логи собираются централизованно.
- [ ] `DATABASE_URL` — отдельная управляемая Postgres-инстанция (RDS/CloudSQL), пользовательский пароль не в репо.
- [ ] Применены миграции: `manage.py migrate --check`.
- [ ] Сервисы развёрнуты раздельно: `api` (gunicorn), `worker-high`, `worker-default`, `worker-low`, `beat`. **`beat` — ровно один инстанс** (он source-of-truth для расписания).
- [ ] LB указывает на `/health/ready` для healthcheck, на `/health/live` — для liveness.
- [ ] `/metrics` доступен только из внутренней сети (или защищён basic-auth).
- [ ] Backup стратегия для Postgres настроена (точка восстановления + retention 14+ дней).
- [ ] Redis настроен с `maxmemory-policy=allkeys-lru` и persistence (RDB или AOF — в зависимости от тоstoy).
- [ ] HSTS, secure cookies, CSRF — обеспечены `prod.py` (см. файл).

## Развёртывание docker-compose (одна машина / dev-stage)

```bash
cp .env.example .env  # отредактировать секреты
docker compose pull
docker compose build
docker compose up -d
docker compose exec api python manage.py migrate
```

Состав сервисов:

| Сервис | Команда | Назначение |
|---|---|---|
| `api` | `gunicorn notify_relay.wsgi` | HTTP-слой, порт 8000 |
| `worker-high` | `celery worker -Q high` | OTP, security-критичные |
| `worker-default` | `celery worker -Q default` | бизнес-уведомления |
| `worker-low` | `celery worker -Q low` | digest, cleanup, metrics |
| `beat` | `celery beat` | расписание (раз в минуту) |
| `db` | `postgres:16` | данные |
| `redis` | `redis:7` | broker + results + rate-limit |
| `mailhog` | `mailhog/mailhog` | dev-only SMTP-стенд |

## Метрики и SLI

`/metrics` отдаёт Prometheus exposition:

- `notify_relay_messages_total{channel,status}` — счётчик финальных переходов (sent / failed / dead).
- `notify_relay_delivery_attempts_total{channel,result}` — счётчик попыток (success / transient_error / permanent_error).
- `notify_relay_delivery_duration_seconds{channel}` — гистограмма времени в `channel.send()`.
- `notify_relay_queue_depth{channel}` — gauge, кол-во messages со статусом `queued` (обновляется Beat'ом каждые 30 с).
- `notify_relay_dlq_size` — gauge, размер dead-letter таблицы.

Рекомендуемые SLI:
- **API success-rate**: процент `2xx` на `POST /messages`. Цель ≥ 99.9%.
- **Delivery P95 latency**: `histogram_quantile(0.95, rate(notify_relay_delivery_duration_seconds_bucket[5m]))`. Цель < 30 с (email) / < 2 с (webhook).
- **Queue saturation**: `notify_relay_queue_depth` стабильно растёт → недостаточно воркеров либо канал лежит.
- **DLQ growth-rate**: `rate(notify_relay_dlq_size[1h])` > 0 → надо разбирать.

## Безопасность

- API-ключи хранятся как `argon2(secret + pepper)`; в БД нет plain-text секретов. Восстановить ключ нельзя — только перевыпустить.
- JWT-токены — короткий access (15 мин) + refresh (7 дней). Подпись HS256.
- Webhook-доставка: проверка URL (запрет приватных подсетей), HMAC-SHA256 подпись тела, no-redirect, таймаут 10 с.
- Rate-limit: 100/мин default + per-key override. Bucket в Redis, atomic Lua.
- Все секреты — через env. `.env` — в `.gitignore`.

## Troubleshooting

| Симптом | Где смотреть | Что делать |
|---|---|---|
| `/health/ready` отдаёт `503` | сам ответ — JSON с `checks` | падает либо `db`, либо `redis`. Проверить `DATABASE_URL` / `REDIS_URL`, доступность портов |
| Сообщения копятся в `queued` | `notify_relay_queue_depth` растёт | проверить, что `worker-default` запущен и не в crashloop; `celery -A notify_relay inspect active` |
| `429` на каждом запросе | `Retry-After` в ответе | бакет исчерпан. Проверить `rate_limit_per_min` ключа в админке; временно отключить `RATE_LIMIT_ENABLED=0` |
| Все webhook'и `422` | в логе `webhook URL resolves to a blocked address range` | хост резолвится в приватную подсеть. Проверить DNS-результат `dig`/`host`; добавить публичный IP получателя в allow-list (через изменение `WEBHOOK_BLOCKED_NETWORKS`) |
| `502/503` на API | gunicorn-логи | вероятно crashloop. `docker compose logs api --tail=200`. Часто — мигратор не отработал |
| `transient_error` на email | `delivery_attempts.smtp_code` | SMTP 4xx — временная проблема (greylisting, rate-limit на стороне MTA). Подождать backoff |
| Beat не дёргает задачи | `worker.*` потребляет `default`-задачи нормально | проверить, что **ровно один** beat-инстанс запущен. Два beat-а → задачи дублируются и тормозят |
| OpenAPI-схема пустая | `/api/schema/` | `drf-spectacular` не нашёл `serializer_class` у view. См. предупреждения в `manage.py spectacular --file /tmp/s.yml` |
