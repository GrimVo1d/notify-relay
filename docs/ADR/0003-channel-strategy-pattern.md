# ADR-0003: Каналы доставки — Strategy pattern с единым `ChannelResult`

**Status:** Accepted
**Date:** 2026-02-28

## Контекст

У нас два канала: email (SMTP) и webhook (outbound HTTP). У обоих своё API, свои коды ошибок, своя семантика «временная vs постоянная» проблема. Логика retry в Celery должна быть одинаковой независимо от канала.

## Решение

Каждый канал реализует протокол `Channel` (`apps/channels/base.py`):

```python
class Channel(Protocol):
    def send(self, message: Message) -> ChannelResult: ...

@dataclass(frozen=True)
class ChannelResult:
    success: bool
    transient: bool        # only meaningful when success=False
    error_message: str = ""
    http_status: int | None = None
    smtp_code: int | None = None
```

Канал — единственное место, где знают про SMTP-коды или HTTP-статусы. Дальше по системе ходит только `ChannelResult`. Логика retry в `_dispatch()`:

- `success=True` → `status=SENT`, всё.
- `success=False, transient=True` → `raise TransientError` → Celery autoretry с exponential backoff.
- `success=False, transient=False` → сразу в `dead_letter`, без retries.

## Последствия

**+** Добавить новый канал = написать класс на ~50 строк + зарегистрировать в `enqueue_for()`. Логика retry / DLQ / metrics не меняется.
**+** Контракт `transient/permanent` явный — нельзя забыть. SMTP 4xx → transient, 5xx → permanent. HTTP 5xx, 408, 425, 429 → transient, остальные 4xx → permanent. Это документировано в коде канала, а не размазано по таскам.
**+** Unit-тесты каналов — это просто моки `send()`. Не нужно дёргать смены статусов в БД.
**−** Каналы не могут возвращать «частичный успех» (например, 1 из 3 получателей доставлен). У нас этого и не нужно — каждое сообщение = один получатель.
**−** Дополнительный слой абстракции (`ChannelResult`) — для одного канала был бы overkill, для двух уже окупается.

## Альтернативы

1. **Прямой вызов SMTP/HTTP из таска, без абстракции.** OK для прототипа. На втором канале начинается копирование retry-логики и расходящаяся семантика ошибок. Через год — рефакторинг ровно к Strategy pattern. Поэтому делаем сразу.
2. **Inheritance вместо Protocol** (`class EmailChannel(BaseChannel)`). Жёстче, но при таком простом интерфейсе structural typing достаточно и даёт лёгкие моки в тестах (`@dataclass` хватает).
3. **Plugin system через entry-points.** Полезно если каналы — это внешние пакеты. Нам это не нужно, оба канала живут в монорепе.

## Связано

- [SPEC §FR-6, §7](../SPEC.md)
- Код: [`src/apps/channels/`](../../src/apps/channels/)
- Использование: [`src/tasks/delivery.py::_dispatch`](../../src/tasks/delivery.py)
