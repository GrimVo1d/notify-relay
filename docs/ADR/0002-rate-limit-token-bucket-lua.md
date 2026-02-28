# ADR-0002: Rate-limit — Redis token-bucket через atomic Lua

**Status:** Accepted
**Date:** 2026-02-28

## Контекст

Нужно ограничить частоту запросов на API-ключ: 100 req/min default + burst 200, индивидуально переопределяемо. Несколько инстансов API за балансировщиком — состояние должно быть общим. Решения два: алгоритм (fixed window / sliding window / token bucket / leaky bucket) и место хранения (in-memory с gossip / Redis / БД).

## Решение

**Алгоритм:** token bucket. **Хранение:** Redis. **Атомарность:** один Lua-скрипт `token_bucket.lua`, который читает счётчик, рефиллит на основе прошедшего времени, проверяет и списывает токены — всё одной операцией на стороне Redis.

Ключ: `rl:<sha256(api_key_full)[:16]>` для аутентифицированных запросов, `rl:ip:<remote_addr>` для анонимных (например, на `/api/v1/auth/token/`, хотя сейчас эти эндпоинты исключены из лимита — у них своя защита).

## Последствия

**+** Burst-friendly: short spikes допустимы, sustained — нет. Это то, что хотят клиенты-сервисы (нет «равномерного RPS», есть batch-волны).
**+** Атомарность Lua → нет TOCTOU между read и decrement. На > 1000 RPS не оверсубскрайбится.
**+** State выживает рестарты воркеров и переезд подов. EXPIRE на ключе чистит мусор.
**−** Lua-скрипт нужно тестировать отдельно (см. `tests/unit/test_token_bucket.py` через `fakeredis[lua]`).
**−** Redis — обязательная зависимость на критическом пути API. При недоступности Redis — middleware либо fail-open (риск перегрузки), либо fail-closed (риск ложных 429). В коде: fail-open + лог, потому что rate-limit — не security-control, а защита downstream'а.

## Альтернативы

1. **Sliding window log.** Точнее, но хранит timestamps всех событий в окне — O(N) памяти и трафика. На burst в 200 это перебор.
2. **Fixed window counter.** Самый простой, но даёт «удвоенный пик» на границе окон (199 в последнюю секунду минуты + 199 в первую секунду следующей).
3. **Leaky bucket.** Эквивалентен token-bucket по эффекту, но менее интуитивный в конфигурации (rate + capacity vs rate + burst — то же самое разным языком).
4. **DRF Throttle (per-view).** Хранит в Django cache. Работает на single-node; для multi-node — нужно `django-redis-cache`. Не атомарно — race condition между чтением и инкрементом. Сами throttle-классы DRF — учебные, не production-grade.
5. **`django-ratelimit` + Redis.** Снаружи похож на наш, внутри — те же race conditions без Lua.
6. **In-memory + gossip.** Слишком сложно для одного сервиса, оверкилл.

## Связано

- [SPEC §FR-6](../SPEC.md)
- Исходник Lua: [`src/apps/ratelimit/lua/token_bucket.lua`](../../src/apps/ratelimit/lua/token_bucket.lua)
- Тесты с fakeredis[lua]: [`tests/integration/test_e2e.py::test_rate_limit_returns_429_with_retry_after`](../../tests/integration/test_e2e.py)
