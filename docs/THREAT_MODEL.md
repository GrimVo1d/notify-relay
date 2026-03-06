# THREAT_MODEL — notify-relay

Threat model using [STRIDE](https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats). Assets: API key data, message templates (may carry PII), outbound webhooks (could be abused to attack internal services).

## Trust boundary

```
                trust boundary
[client service] ──TLS──> [API ingress] ──> [API pod] ──> [Postgres]
                                              │
                                              ├──> [Redis (broker, ratelimit)]
                                              ├──> [Worker pods] ──> SMTP / external HTTPS
                                              └──> [Beat pod]
```

Everything inside the `trust boundary` (past the ingress) lives in a private subnet. Only workers reach outwards, and only for their stated jobs.

## Threats and mitigations

| ID | STRIDE category | Scenario | Mitigation |
|----|----|----|----|
| T-01 | **S**poofing | Forging `X-API-Key` to impersonate another service | `ApiKeyAuthentication`: argon2 + pepper, constant-time verify. Minimum 24 bytes of entropy in the secret. See [ADR-0001](ADR/0001-idempotency-storage.md). |
| T-02 | **S**poofing | Attacker steals an access JWT and tries to pose as staff | Short access TTL (15 min), refresh blacklist via `TokenBlacklistView` (TODO: add the SimpleJWT blacklist app). HS256 signed with `JWT_SIGNING_KEY` from env. |
| T-03 | **T**ampering | MitM tampers with the webhook body at the recipient | `X-Notify-Signature: sha256=...` HMAC. Receiver verifies it against the shared secret. See [`apps/channels/security.py`](../src/apps/channels/security.py). |
| T-04 | **T**ampering | A send request with a malicious `context` that triggers RCE via the template | The Jinja2 `SandboxedEnvironment` blocks `__class__`/`__mro__` escapes. See ADR-0005. |
| T-05 | **R**epudiation | "I never sent that notification" / "it never arrived" | `delivery_attempts` stores every attempt with http_status/smtp_code, timestamps, and `error_message`. The request id in logs ties the API call to its task and attempt. |
| T-06 | **I**nformation disclosure | API key leaked through logs | Only the `prefix` is logged, never the full key. The secret lives only as an argon2 hash. The plain value is shown once — on the `POST /api-keys` response. |
| T-07 | **I**nformation disclosure | Leaking other people's messages through `GET /messages/{id}` | `MessageViewSet.get_queryset()` filters by `request.auth` (API key) — you cannot see someone else's message. Staff sees everything. Covered by `test_message_get_is_scoped_to_owner_api_key`. |
| T-08 | **I**nformation disclosure | Leak via timing attack against argon2 | `passlib.argon2.verify` uses constant-time comparison. |
| T-09 | **D**enial of service | Flood `POST /messages` to clog the queue | Token-bucket rate-limit per API key (`X-RateLimit-Remaining` + 429 + `Retry-After`). Celery queues with bounded prefetch (`CELERY_WORKER_PREFETCH_MULTIPLIER=1`). See [ADR-0002](ADR/0002-rate-limit-token-bucket-lua.md). |
| T-10 | **D**enial of service | Adversary creates many API keys from a staff account to amplify the flood | API-key creation is staff-only. Staff accounts are managed manually. Handled at the org level. |
| T-11 | **D**enial of service | Huge renders (massive context) → CPU burn in the API pod | Body-size limit at the ingress (LB / gunicorn `--limit-request-line/--limit-request-fields`). In code: no render larger than a 1MB template; we plan to enforce a template_version size cap on create (TODO). |
| T-12 | **E**levation of privilege | Webhook URL = `http://internal-service/admin/users` → relay calls it from inside the VPC | **SSRF guard**: `validate_webhook_url()` blocks private subnets (`127/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`, `::1`, `fc00::/7`) and resolves DNS pre-send. Two layers: at message creation (api) and at send (worker). |
| T-13 | **E**levation of privilege | HTTP client follows a 30x redirect to an internal address | `httpx.post(..., follow_redirects=False)`. See [`webhook.py`](../src/apps/channels/webhook.py). |
| T-14 | **E**levation of privilege | Replay of an intercepted webhook against the recipient | `X-Notify-Timestamp` header (TODO: include it in the signature; today it's sent separately). The recipient should reject timestamps older than N minutes. |
| T-15 | **I**nformation disclosure | `dead_letter.payload_snapshot` retains PII indefinitely | The `cleanup_old_messages` beat task clears both `messages` and cascading `dead_letter` (FK CASCADE). 90 day retention, configurable. |

## Residual risks

1. **Compromised staff account** → creates new API keys with large rate limits and floods email channels. Mitigation: 2FA on staff (outside this service), audit log on key create/use (TODO in ADR-0006).
2. **Compromised SMTP relay credentials** → an external actor sends mail as our domain. Mitigation: SPF/DKIM/DMARC at the mail-domain level (org task).
3. **Compromised webhook secret** (`WEBHOOK_HMAC_SECRET`) → the recipient cannot tell forgeries from real traffic. Roadmap mitigation: per-recipient secret instead of a global one, with rotation via the recipient UI.

## Pre-prod security checklist

- [ ] All `dev-insecure-*` secrets replaced.
- [ ] `WEBHOOK_BLOCKED_NETWORKS` matches the actual private subnets in the VPC (not just the default set).
- [ ] DNS resolver on worker pods does not return internal domains that resolve to public IPs (DNS rebinding).
- [ ] Request body size cap at the ingress: ≤ 64KB for `POST /messages`.
- [ ] CSP / `X-Content-Type-Options: nosniff` / `Strict-Transport-Security` set at the ingress.
- [ ] `/metrics` restricted to internal-only or basic-auth.
- [ ] Logs centralized, retention ≥ 30 days, access audited.
- [ ] `pip-audit` / dependabot scheduled — CVE tracking.
