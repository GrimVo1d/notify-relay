# THREAT_MODEL — notify-relay

Модель угроз по [STRIDE](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats). Активы: данные API-ключей, шаблоны сообщений (могут содержать PII), исходящие webhook'и (могут быть использованы для атаки на внутренние сервисы).

## Границы доверия

```
                trust boundary
[client service] ──TLS──> [API ingress] ──> [API pod] ──> [Postgres]
                                              │
                                              ├──> [Redis (broker, ratelimit)]
                                              ├──> [Worker pods] ──> SMTP / external HTTPS
                                              └──> [Beat pod]
```

Всё внутри `trust boundary` (после ingress) — в private subnet. Snitch'ить наружу могут только workers (по своему делу).

## Угрозы и митигации

| ID | Категория STRIDE | Сценарий | Защита |
|----|----|----|----|
| T-01 | **S**poofing | Подделка `X-API-Key` запроса под чужой сервис | `ApiKeyAuthentication`: argon2 + pepper, constant-time verify. Минимум 24 байта entropy в секрете. См. [ADR-0001](ADR/0001-idempotency-storage.md). |
| T-02 | **S**poofing | Атакующий получил access JWT и пытается выдать себя за staff | Короткий TTL access (15 мин), refresh blacklist через TokenBlacklistView (TODO: добавить SimpleJWT blacklist app). Подпись HS256 с `JWT_SIGNING_KEY` в env. |
| T-03 | **T**ampering | MitM подменяет тело webhook'а у получателя | `X-Notify-Signature: sha256=...` HMAC. Получатель валидирует через shared secret. См. [`apps/channels/security.py`](../src/apps/channels/security.py). |
| T-04 | **T**ampering | Запрос на отправку с malicious payload в `context`, который через шаблон выполнит arbitrary code (RCE через шаблон) | `django.template.Template` не даёт `__class__`/`__mro__` доступа. Дополнительный планируемый upgrade — Jinja2 `SandboxedEnvironment` (см. [ADR-0005 TODO]). |
| T-05 | **R**epudiation | «Я не отправлял это уведомление» / «оно не пришло» | `delivery_attempts` хранит все попытки с http_status/smtp_code, timestamps и error_message. Request-ID в логах коррелирует API-вызов с задачей и попыткой. |
| T-06 | **I**nformation disclosure | Утечка API-key через логи | Логируем только `prefix`, никогда полный ключ. Секрет хранится только argon2-хешем. Само значение видно один раз — на ответе `POST /api-keys`. |
| T-07 | **I**nformation disclosure | Утечка чужих сообщений через `GET /messages/{id}` | `MessageViewSet.get_queryset()` фильтрует по `request.auth` (API-key) — невозможно увидеть чужое сообщение. Staff видит всё. Проверено в `test_message_get_is_scoped_to_owner_api_key`. |
| T-08 | **I**nformation disclosure | Утечка через тайминг (timing attack на argon2) | `passlib.argon2.verify` использует constant-time сравнение. |
| T-09 | **D**enial of service | Flood `POST /messages` с целью забить очередь | Token-bucket rate-limit per API-key (`X-RateLimit-Remaining` + 429 + `Retry-After`). Очереди Celery с bounded prefetch (`CELERY_WORKER_PREFETCH_MULTIPLIER=1`). См. [ADR-0002](ADR/0002-rate-limit-token-bucket-lua.md). |
| T-10 | **D**enial of service | Адверсарий регистрирует много API-key'ев со staff-аккаунта → суммарно flood | Создание API-key — только staff. Staff-аккаунты управляются вручную. На уровне организационном. |
| T-11 | **D**enial of service | Большие render'ы (огромный context) → CPU burn в API-поде | Лимит размера тела запроса на ingress (LB / gunicorn `--limit-request-line/--limit-request-fields`). В коде — нет рендера >1MB шаблона: проверка размера template_version при создании (TODO). |
| T-12 | **E**levation of privilege | Webhook URL = `http://internal-service/admin/users` → relay делает запрос изнутри VPC | **SSRF guard**: `validate_webhook_url()` блокирует приватные подсети (`127/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`) и резолвит DNS до отправки. Защита на двух уровнях: при создании сообщения (api) и при отправке (worker). |
| T-13 | **E**levation of privilege | `requests` следует за 30x редиректом с внутреннего адреса | `requests.post(..., allow_redirects=False)`. См. [`webhook.py`](../src/apps/channels/webhook.py). |
| T-14 | **E**levation of privilege | Replay перехваченного webhook'а получателем | `X-Notify-Timestamp` header передаётся в подпись (TODO: добавить в подпись, сейчас отдельно). Получатель должен отвергать timestamps старше N минут. |
| T-15 | **I**nformation disclosure | `dead_letter.payload_snapshot` хранит PII бесконечно | Cleanup beat-задача `cleanup_old_messages` чистит и сами `messages`, и каскадно `dead_letter` (FK CASCADE). Retention 90 дней. Конфигурируется. |

## Остаточные риски

1. **Compromised staff account** → создание новых API-ключей с большим rate limit + распыление спама через канал email. Митигация — 2FA на staff (за пределами этого сервиса), audit log на создание/использование ключей (TODO в ADR-0006).
2. **Compromised SMTP relay credentials** → внешний актёр шлёт email от нашего домена. Митигация — SPF/DKIM/DMARC на уровне почтового домена (организационная задача).
3. **Compromised webhook secret** (`WEBHOOK_HMAC_SECRET`) → получатель не сможет отличить подделку от настоящего. Митигация в roadmap: per-recipient secret вместо глобального, rotation через UI получателя.

## Чеклист безопасности перед прод-релизом

- [ ] Все `dev-insecure-*` секреты заменены.
- [ ] `WEBHOOK_BLOCKED_NETWORKS` соответствует фактическим приватным подсетям VPC (не только дефолтному набору).
- [ ] DNS-резолвер на воркер-подах не имеет внутренних доменов, резолвящихся в публичные IP (DNS rebinding).
- [ ] Лимит размера тела на ingress: ≤ 64KB для `POST /messages`.
- [ ] CSP / `X-Content-Type-Options: nosniff` / `Strict-Transport-Security` — на ingress.
- [ ] `/metrics` за internal-only или basic-auth.
- [ ] Logs centralized, retention ≥ 30 дней, доступ по аудиту.
- [ ] Регулярный `pip-audit` / dependabot — отслеживание CVE.
