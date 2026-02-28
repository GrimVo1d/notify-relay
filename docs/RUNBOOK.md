# RUNBOOK — notify-relay

Операционный playbook для on-call. Ориентирован на алерты из Prometheus / Grafana (`docs/grafana-dashboard.json`).

## Правила работы

1. Сначала зафиксируй симптом и время — скриншот / лог. Только потом чини.
2. Хочешь покрутить настройки в проде — открой PR с изменением `.env` / settings, апрувь, релизь. Никаких ручных `kubectl set env` без следа в git.
3. Если кажется, что вытащить пробку прямо сейчас можно только опасным действием — сначала эскалация: позвонить дежурному senior'у.

---

## Алерт: `APIHealthDegraded` (`/health/ready` отдаёт 503)

**Что значит:** API-под не может достучаться до PG или Redis.

**Чек-лист:**
1. Открой сам ответ `/health/ready` — там JSON с `checks.db` / `checks.redis`. Один из них = error.
2. Если `db` лежит:
   - Проверь Postgres-инстанс: `kubectl logs -l app=postgres --tail=200` (или RDS console).
   - Подключись локально: `psql $DATABASE_URL -c 'SELECT 1'`.
   - Если PG отвечает, но API не подключается — проверь `pg_stat_activity` на исчерпание connections. Дефолт PG = 100. Если занято — увеличить пул на стороне приложения **уменьшить**, не увеличить (см. ниже).
3. Если `redis` лежит:
   - `redis-cli -u $REDIS_URL ping`.
   - Проверь `maxmemory` / OOM в логах. Если bucket-ключи переполнили память — увеличить `maxmemory` или сменить `maxmemory-policy` на `allkeys-lru` (см. `docs/DEPLOYMENT.md`).
4. Если оба ОК, а `/health/ready` всё равно красит — рестарт api-пода (`kubectl rollout restart deploy/api`). Stale connection pool возможен.

**Когда успокоиться:** `/health/ready` стабильно 200 в течение 60 секунд.

---

## Алерт: `MessagesPilingUp` (`notify_relay_queue_depth` растёт >5 мин)

**Что значит:** API принимает быстрее, чем воркеры успевают доставлять.

**Чек-лист:**
1. На какой канал? — посмотри `notify_relay_queue_depth{channel="email"}` vs `{channel="webhook"}`.
2. **Email канал отстаёт** — почти всегда внешняя проблема:
   - `kubectl logs -l app=worker-default | grep -i smtp` — ищи `4xx temporary failure`.
   - Если MTA нас зарейтлимитил — снизить throughput у нас или попросить лимит у провайдера. Backoff в наших ретраях сам разгребёт.
3. **Webhook канал отстаёт** — обычно ровно один получатель «лёг»:
   - Группировка по `recipient` через лог: `kubectl logs ... | jq 'select(.message=="webhook_attempt") | .recipient' | sort | uniq -c | sort -rn | head`.
   - Если один URL дает >50% ошибок — связаться с владельцем сервиса или временно disable их подписку (если будет фича; сейчас — нет, только через DB UPDATE).
4. Если оба канала — недостаточно воркеров. Scale: `kubectl scale deploy/worker-default --replicas=8`.

**Когда успокоиться:** `queue_depth` стабильно <100, тренд вниз.

---

## Алерт: `DeadLetterGrowing` (`rate(notify_relay_dlq_size[1h]) > 0`)

**Что значит:** сообщения регулярно исчерпывают 6 retries и падают в DLQ.

**Чек-лист:**
1. По какому каналу растёт DLQ? — JOIN с `notify_relay_messages_total{status="dead"}` по channel.
2. **email** — обычно почтовый домен получателя отбойник (`421 too many connections`, `550 mailbox not found`). Поднять в DB причины:
   ```sql
   SELECT m.recipient, dl.reason, count(*)
   FROM messages m JOIN dead_letter dl ON dl.message_id = m.id
   WHERE dl.created_at > now() - interval '1 hour'
   GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
   ```
3. **webhook** — endpoint получателя лежит. Связь с владельцем.
4. Технически разрезать DLQ можно: восстановить статус и переенкьюить:
   ```sql
   UPDATE messages SET status='queued', updated_at=now()
   WHERE id IN (SELECT message_id FROM dead_letter WHERE created_at > now() - interval '10 minutes');
   ```
   и запустить `dispatch_scheduled` вручную (`celery -A notify_relay call tasks.scheduler.dispatch_scheduled`).

**Когда успокоиться:** rate(DLQ) = 0 в течение 15 минут.

---

## Алерт: `RateLimitSpike` (массовые 429 на одном API-ключе)

**Что значит:** клиент превысил бакет. Это **не наш** баг, но проверить надо.

**Чек-лист:**
1. Кто? — найти ключ по `prefix` в логах: `kubectl logs -l app=api | grep '"status":429' | jq '.api_key_prefix' | sort | uniq -c`.
2. Связаться с владельцем сервиса. Возможные причины: миграция с другой системы (burst), цикл retry на их стороне (баг).
3. Если правомерный кейс — поднять им лимит через админку (`/admin/core/apikey/`).

---

## Алерт: `OpenWebhookConnections` (>50 одновременных POST'ов в worker'е)

**Что значит:** медленный webhook-получатель держит соединения. Если их сотни — таймауты `requests` могут не срабатывать (рассогласование между requests timeout и системным TCP keepalive).

**Чек-лист:**
1. `kubectl exec worker-default -- ss -tn | grep ESTAB | wc -l`.
2. Если конкретный IP — выяснить, наш ли это получатель (по DNS).
3. Тяжёлый случай — рестартнуть воркер. Сообщения переедут на retry.

---

## «Что делать, если…»

| Симптом | Первое подозрение |
|---|---|
| `502/503` от nginx | API-под в crashloop — `kubectl describe pod` |
| Все email `transient_error` | SMTP сервер лежит или auth не проходит |
| Все webhook `permanent_error` 422 | `WEBHOOK_BLOCKED_NETWORKS` слишком жёсткий, или DNS подсунул приватный IP |
| Beat не дёргает задачи | Не один beat работает, а два (race) или ни одного |
| `OpenAPI` пуст | drf-spectacular не видит viewset'ы — посмотреть `manage.py spectacular --file /tmp/s.yml 2>&1` |
| `/metrics` пуст | prometheus_client не инициализирован — проверить что middleware/metrics imported |
| Тесты падают локально, в CI зелёные | Скорее всего разные версии зависимостей — `pip install -e .[dev] --force-reinstall` |

---

## Эскалация

| Кто | Когда |
|---|---|
| On-call backend lead | Невозможность доставить более 30 минут, любой инцидент security |
| Inframind / DevOps | Проблемы с PG / Redis инфраструктурой |
| Product | Решения о disable'е канала / получателя без их ведома |
