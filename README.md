# notify-relay

Транзакционный сервис рассылок: принимает запросы через REST API, помещает их в Celery-очередь и доставляет получателям по двум каналам — **email (SMTP)** и **outbound webhook (HTTP POST + HMAC)**. Поддерживает приоритеты, ретраи, идемпотентность, шаблоны сообщений и rate-limit на отправителя.

## Зачем

Чтобы прикладные сервисы (биллинг, аутентификация, заказы) не тащили внутрь себя SMTP-клиенты и логику ретраев, а отдавали уведомления одному надёжному relay-сервису через HTTP.

## Стек

Python 3.12 · Django 5 · DRF · PostgreSQL 16 · Redis 7 · Celery 5 · Docker · GitHub Actions

## Быстрый старт (после реализации)

```bash
cp .env.example .env
docker compose up -d
docker compose exec api python manage.py migrate
docker compose exec api python manage.py createsuperuser
# API: http://localhost:8000/api/v1/
# OpenAPI: http://localhost:8000/api/schema/swagger-ui/
```

## Документация

- [docs/SPEC.md](docs/SPEC.md) — техническое задание
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — компоненты и потоки данных
- [docs/ROADMAP.md](docs/ROADMAP.md) — пошаговый план реализации
