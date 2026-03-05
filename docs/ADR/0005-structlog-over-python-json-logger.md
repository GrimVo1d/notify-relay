# ADR-0005: Structured logging via structlog

**Status:** Accepted (supersedes earlier `python-json-logger` choice)
**Date:** 2026-03-05

## Контекст

Изначально (step-05) JSON-логирование делалось через `python-json-logger.JsonFormatter` — наследовался класс, в `add_fields` добавлялись `level`, `logger`, `request_id` из `ContextVar`. Это работало для базового сценария «один request_id», но при выходе за пределы web-запроса (Celery-таска без request scope, фоновый процесс) становилось неудобно — для каждого нового контекста приходится отдельно создавать ContextVar и руками таскать его в логи.

Сравнение мейнтененса: `python-json-logger` имеет ~600k скачиваний в день и поддерживается, но фактически замер на min-viable features. `structlog` — 6M/день, активная разработка, и в 2024+ де-факто стандарт structured logging в Python вне Django.

## Решение

Переключаемся на `structlog` (см. коммит `refactor(core): swap python-json-logger for structlog with bound contextvars`). Конкретно:

1. **Pipeline.** Единый `ProcessorFormatter` для stdlib-логгеров и `structlog.get_logger()` — оба идут через одни и те же processors (timestamp, level, request_id, exc_info, JSON-render).
2. **Контекст.** `structlog.contextvars.bind_contextvars(request_id=..., user_id=...)` вместо ручного ContextVar. Любые поля биндятся одной строкой, попадают во ВСЕ записи в этом контексте, и так же легко сбрасываются tokens'ами.
3. **Два renderer'а.** `JSONRenderer()` для prod, `ConsoleRenderer(colors=False)` для dev. Переключается через env `LOG_FORMAT`.

## Последствия

**+** Унифицированный пайплайн: один и тот же лог-вывод для `logging.getLogger`, `structlog.get_logger`, и `print → captured by logger` paths. Раньше JsonFormatter применялся только к stdlib-логам.
**+** Бесплатные структурированные поля: `log.info("delivery.attempt", message_id=..., channel=..., result=...)` сразу даёт JSON-объект с этими ключами. Не надо писать `extra={...}` или конкатенировать.
**+** Dev-опыт лучше: ConsoleRenderer показывает поля в плоском key=value виде с подсветкой, читается без `jq`.
**−** Чуть больше boilerplate в `apps/core/logging.py` (одно подключение `configure_structlog()` в `AppConfig.ready()`).
**−** Новые контрибьюторы должны знать оба API: `logging.getLogger` (для существующего кода) и `structlog.get_logger` (предпочтительный для новых модулей). На практике не путаются — оба пишут в один поток.

## Альтернативы

1. **Остаться на `python-json-logger`.** Работает, но требует пилить расширение ContextVar под каждый кейс. Cтало некомфортно после первой Celery-задачи, которой нужны свои bound-поля.
2. **`loguru`.** Более «магический» (auto-rotation, threads-on-import). Не дружит с Django LOGGING dict-конфигом без манёвров. Слишком много opinionated дефолтов.
3. **`opentelemetry-sdk` с OTLP exporter в Loki/Grafana.** Это правильно делать одновременно с трейсингом, но это отдельный этап (см. roadmap «middle+ extra»). Сейчас остаёмся на Prometheus-метриках + structured stdout-логи.

## Миграционные заметки

- Старый код, делавший `set_request_id(value) → token` / `reset_request_id(token)`, продолжает работать — API сохранён, только под капотом теперь `structlog.contextvars.bind/reset_contextvars`.
- Новый код может писать напрямую `structlog.get_logger(__name__).info("event_name", **fields)` и получать JSON автоматически.

## Связано

- [ADR-0001](0001-idempotency-storage.md) — тот же подход «один источник правды»
- Коммит `refactor(core): swap python-json-logger for structlog with bound contextvars`
- [`src/apps/core/logging.py`](../../src/apps/core/logging.py)
