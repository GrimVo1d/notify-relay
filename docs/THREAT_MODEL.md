# Threat model

## Assets and trust boundaries

- **Assets:** API keys and webhook secrets, message contents (recipients, context: often
  personal data), the ability to send mail from the organisation's domain, network position
  inside the deployment (reachability of internal services).
- **Callers:** internal services holding an API key (trusted to send, not to read others'
  messages); staff users (trusted, manage templates and keys); everyone else (untrusted).
- **Outbound:** an SMTP relay and arbitrary webhook URLs chosen by callers (untrusted
  destinations).

## Threats and mitigations

| ID | Threat | Mitigation |
|---|---|---|
| T-01 | Guessing or forging an API key | 192-bit random secret; stored only as `HMAC-SHA256(pepper, secret)`; constant-time compare. Lookup by the separate `key_id`, so verification costs one row and one HMAC. |
| T-02 | Brute-forcing staff passwords | Per-IP bucket on `/api/v1/auth/` and `/admin/login/` (10/min by default); with `NUM_PROXIES` the real client IP is used behind proxies. |
| T-03 | Stolen staff JWT | 15-minute access tokens signed with a dedicated `JWT_SIGNING_KEY`. Refresh tokens can't be revoked before expiry (7 days): see residual risks. |
| T-04 | Reading other services' messages | `GET /messages/{id}` is scoped to the calling key; ids are ULIDs, not sequential. |
| T-05 | Idempotency collisions between callers | Idempotency is scoped per API key, and only API keys can create messages. |
| T-06 | Template injection / sandbox escape | Jinja2 `SandboxedEnvironment`; templates are staff-only; any render failure is a 422; template size and output size are capped. |
| T-07 | HTML injection into mail through `context` | Email bodies are autoescaped and sent as `text/html`. |
| T-08 | SSRF: webhook to an internal service or cloud metadata | Only globally routable, non-multicast targets; NAT64, 6to4, Teredo, IPv4-compatible, SIIT and site-local IPv6 ranges refused; optional extra `WEBHOOK_BLOCKED_NETWORKS`. Checked at creation and at send. |
| T-09 | DNS rebinding between check and connect | The worker resolves once and connects to the vetted address, keeping `Host` and SNI. |
| T-10 | Redirect to an internal address; proxy variables | Redirects are never followed (3xx is a permanent failure); `trust_env=False`. |
| T-11 | Webhook to the wrong domain through IDN mapping differences (`faß` vs `fass`) | The IDNA 2008 A-label is resolved and sent in both `Host` and SNI. |
| T-12 | A receiver exhausting worker memory with a huge or endless response | The response body is never read; only the status line and headers. |
| T-13 | A slow name server holding API workers during validation | The API-side lookup gives up after 2 s and never queues; the URL is then checked only at send time, which is the check that decides the target. |
| T-14 | Forged or tampered webhooks at the receiver | `X-Notify-Signature` = HMAC-SHA256 over timestamp and body with a per-key secret. |
| T-15 | Replay of a captured webhook | The timestamp is signed; receivers reject old timestamps and de-duplicate on `X-Notify-Message-Id`. |
| T-16 | Flooding the queue | Token bucket per API key, per IP for everything else; Celery prefetch 1. An `X-Forwarded-For` entry that isn't an IP address is ignored; IPv6 clients are bucketed by /64. |
| T-17 | Misconfigured production (debug, default secrets, `ALLOWED_HOSTS=*`) | The `prod` profile refuses to start; it is the image default. |
| T-18 | Leaking internals in errors | `DEBUG` off in `prod`; readiness reports only `ok`/`error`, details go to the log; webhook failures record only the status code. |
| T-19 | Supply chain | Dependencies pinned with hashes and audited with `pip-audit` (`make audit`); base image pinned by digest; pre-commit hooks pinned to commit SHAs. |
| T-20 | Container breakout impact | Non-root user, code owned by root, no compilers or curl in the runtime image. |
| T-21 | Exposure of the dev stack | Compose binds every port to 127.0.0.1. |
| T-22 | Personal data kept forever | Finished messages, attempts and dead letters are deleted after 90 days. |

## Residual risks

1. **Unauthenticated metrics.** `/metrics` and the workers' metrics port expose queue sizes and
   delivery rates. They must stay on the monitoring network.
2. **No JWT revocation.** A leaked refresh token works until it expires. Adding simplejwt's
   blacklist app would allow revoking it.
3. **Compromised staff account.** It can issue keys with high limits and send mail as the
   organisation. Mitigate with SSO/2FA in front of the admin and SPF/DKIM/DMARC on the domain.
4. **Per-IP limits behind proxies.** If `NUM_PROXIES` is wrong, or the API is reachable around
   the proxy, clients either share one bucket (too low) or can pick their IP through
   `X-Forwarded-For` (too high). The same applies to `X-Forwarded-Proto`.
5. **Rate limiter fails open.** While Redis is down nothing is limited; the readiness probe
   reports it.
6. **At-least-once delivery.** A worker crash after sending and before recording can repeat a
   delivery; receivers de-duplicate on the message id. A retry lost by the broker leaves the
   message in `sending`; Beat puts it back in the queue after 2 hours, so it arrives late.
7. **Probe cost.** `/health/ready` answers any `Host` without a rate limit and runs a database
   query and a Redis ping per call; keep it off the public ingress.
