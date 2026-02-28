# PERFORMANCE — notify-relay

Цели по производительности, capacity-математика, и куда смотреть для тюнинга.

## SLO (Service Level Objectives)

| Метрика | Цель |
|---|---|
| `POST /messages` P95 latency | < 150 ms (postgres insert + enqueue, без внешнего IO) |
| `POST /messages` success rate (2xx или 4xx по делу) | ≥ 99.9% за 28 дней |
| Email delivery P95 (queue → SMTP accept) | < 30 s |
| Webhook delivery P95 (queue → HTTP 2xx) | < 2 s |
| Сообщений `queued` старше 5 мин | 0 (вне инцидентов) |

## Capacity на одну ноду

Конфиг базовой ноды: 4 CPU / 8 GB RAM. Сервисы делят узлы:

| Поды на узле | CPU req | Mem req |
|---|---|---|
| api × 1 (gunicorn 4 workers, sync) | 1.5 | 1 GB |
| worker-high × 1 (concurrency=8) | 0.5 | 0.5 GB |
| worker-default × 1 (concurrency=4) | 0.5 | 0.5 GB |
| worker-low × 1 (concurrency=2) | 0.2 | 0.3 GB |
| beat × 1 | 0.1 | 0.1 GB |

### Throughput API

Sync-gunicorn под Django + 1 PG insert на запрос (с обвязкой):

```
T_request ≈ T_validation + T_render + T_db_insert + T_redis_enqueue
         ≈ 5 ms   + 2 ms   + 8 ms          + 1 ms          ≈ 16 ms
```

С 4 worker'ами sync: 4 × (1000 / 16) ≈ **250 RPS на ноду**. SLO 200 RPS → запас 25%.

Узкие места при росте:
- PG connections: gunicorn 4 worker × `CONN_MAX_AGE` без pgbouncer = 4 коннекта. На 5 нод = 20. PG `max_connections=100` → потолок ~25 нод без pgbouncer. **С pgbouncer (transaction pooling) — практически неограниченно** для нашей нагрузки.
- Шаблонный рендер не кешируется (`Template(src)` каждый раз). На 200 RPS со средней шаблонкой это ~400 ms CPU/секунду на ноду — **3% от 4 CPU**, оставляем как есть.

### Throughput Email

Один SMTP отправка = ~150 ms на handshake + ~50 ms на DATA. С concurrency=8 в worker-high и concurrency=4 в worker-default:

```
worker-high:    8 / 0.2 s ≈ 40 email/sec
worker-default: 4 / 0.2 s ≈ 20 email/sec
Σ              ≈ 60 email/sec ≈ 3600 email/min
```

Реальный bottleneck — внешний MTA. Большинство провайдеров (Postmark, SES) лимитируют ≈ 14 email/sec на аккаунт без warm-up. Нужен либо больший лимит у провайдера, либо параллельные кредиты от нескольких.

### Throughput Webhook

Webhook = 1 HTTP-POST с timeout 10 с, обычно отрабатывает < 200 ms. С `concurrency=4 (worker-default)` ≈ 20 webhook/sec, ≈ 1200/min.

Если получатели медленные (P95 > 1 с) — concurrency не помогает, нужно поднимать `--concurrency` пропорционально. Но осторожно: каждый concurrent worker — это коннект к PG, см. лимит выше.

## Профили нагрузки на БД

| Запрос | Частота | Стоимость | Индекс |
|---|---|---|---|
| `INSERT INTO messages` | ~ RPS | ~5 ms | PK (ULID), unique `(api_key_id, idempotency_key)` |
| `SELECT ... WHERE api_key_id=? AND idempotency_key=?` (idempotency lookup) | ~ RPS | ~1 ms | unique idx (выше) |
| `SELECT ... WHERE status='queued' AND scheduled_at<=now()` (Beat) | 1/min | зависит от queue_depth | `message_status_sched_idx` (status, scheduled_at) |
| `SELECT ... WHERE created_at < now() - interval '90 days'` (cleanup) | 1/day | full scan по этой строки нет, идёт через индекс | `message_created_idx` |

### Что должно показывать `EXPLAIN ANALYZE`

Idempotency lookup на горячих данных:
```
Index Scan using messages_message_idempotency_unique on messages
  Index Cond: ((api_key_id = $1) AND (idempotency_key = $2))
Planning Time: 0.1 ms
Execution Time: < 1 ms
```

Если вместо `Index Scan` видишь `Seq Scan` — индекс не используется. Проверить статистику (`ANALYZE messages`), типы параметров.

Beat dispatch:
```
Index Scan using message_status_sched_idx on messages
  Index Cond: ((status = 'queued') AND (scheduled_at <= now()))
  Rows Removed by Filter: 0
Limit  (cost=... rows=200)
```

При больших `queue_depth` (> 100k) — добавить `partial index`:
```sql
CREATE INDEX message_pending_idx
ON messages (scheduled_at)
WHERE status = 'queued';
```

## Микро-бенчмарки

Локальный прогон `pytest-benchmark` на критических местах (планируется в next iteration):

```
bench_hmac_signature:        ~5 µs/op        (negligible)
bench_template_render:       ~80 µs/op       (caching uncritical)
bench_argon2_verify:         ~25 ms/op       (intentionally slow, 4 rounds)
bench_idempotency_lookup:    ~700 µs/op      (PG over loopback)
```

`bench_argon2_verify` — самая дорогая операция, поэтому она не on the hot path API (rate-limit middleware использует HASH(api_key)[:16] для bucket-identity, **не** argon2 verify). Argon2 verify запускается только в DRF auth, который кешируется DRF'ом на запрос.

## Load test (planned)

`locust` или `k6`. Стенд:

- 1 нода (api + worker-default), Postgres / Redis на той же машине.
- 1k pre-seeded `ApiKey`, 10 pre-seeded `TemplateVersion`.
- Сценарий «happy path»: 80% — `POST /messages` с email-каналом, 20% — webhook на эхо-сервер (`https://httpbin.org/status/200`).

Целевые цифры:
- P95 `POST` < 150 ms при 200 RPS — **зелёный**
- P95 `POST` < 500 ms при 400 RPS — **серый, точка деградации**
- Stable error rate < 0.1% — **зелёный**

Результаты прогона будут опубликованы в [`docs/LOAD_TEST_RESULTS.md`](LOAD_TEST_RESULTS.md) (после реализации load-test инструментария).

## Тюнинг под нагрузку — чек-лист

В порядке возрастания усилий:

1. **+pgbouncer** перед PG — снимает потолок connections.
2. **Materialized View** для аналитики (кол-во messages по `status` за период) — не для горячего пути, но для админских дашбордов.
3. **Кеш TemplateVersion** — read-mostly, изменяется редко. `lru_cache(1024)` по `(name, version)`.
4. **Async Django view** для `POST /messages` (Django 5 ASGI + asyncpg) — но это требует переписать middleware и services под async. Выигрыш заметен только при >500 RPS на нод. **Не делать преждевременно.**
5. **Партиционирование `messages`** по `created_at` (monthly) — когда таблица >100 GB.
