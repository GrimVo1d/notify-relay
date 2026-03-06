# RUNBOOK — notify-relay

On-call playbook. Aligned with the Prometheus / Grafana alerts in `docs/grafana-dashboard.json`.

## Ground rules

1. Capture the symptom and timestamp first — screenshot or log line. Then fix.
2. Want to tweak production settings? Open a PR that changes `.env` / settings, get it reviewed, and roll it out. No ad-hoc `kubectl set env` without a paper trail in git.
3. If the only fix you can think of right now is a dangerous action — escalate first: page the on-call senior.

---

## Alert: `APIHealthDegraded` (`/health/ready` returns 503)

**What it means:** the API pod cannot reach Postgres or Redis.

**Checklist:**
1. Read the `/health/ready` response — it returns JSON with `checks.db` / `checks.redis`. One of them is `error`.
2. If `db` is down:
   - Inspect the Postgres instance: `kubectl logs -l app=postgres --tail=200` (or the RDS console).
   - Connect locally: `psql $DATABASE_URL -c 'SELECT 1'`.
   - If PG answers but the API can't connect — check `pg_stat_activity` for exhausted connections. PG default is 100. If maxed out, **shrink** the app-side pool, don't grow it (see DEPLOYMENT.md).
3. If `redis` is down:
   - `redis-cli -u $REDIS_URL ping`.
   - Check `maxmemory` / OOM in the logs. If bucket keys overflowed memory, raise `maxmemory` or switch `maxmemory-policy` to `allkeys-lru` (see `docs/DEPLOYMENT.md`).
4. If both look OK but `/health/ready` is still red — restart the api pod (`kubectl rollout restart deploy/api`). Stale connection pools happen.

**When to call it green:** `/health/ready` stays 200 for 60 seconds.

---

## Alert: `MessagesPilingUp` (`notify_relay_queue_depth` grows for >5 min)

**What it means:** the API is accepting faster than workers can deliver.

**Checklist:**
1. Which channel? — compare `notify_relay_queue_depth{channel="email"}` vs `{channel="webhook"}`.
2. **Email channel lagging** — usually external:
   - `kubectl logs -l app=worker-default | grep -i smtp` — look for `4xx temporary failure`.
   - If the MTA rate-limited us — reduce our throughput or request a higher quota from the provider. Our backoff will catch up on its own.
3. **Webhook channel lagging** — usually one recipient went down:
   - Group by `recipient` from logs: `kubectl logs ... | jq 'select(.message=="webhook_attempt") | .recipient' | sort | uniq -c | sort -rn | head`.
   - If one URL accounts for >50% of errors — reach out to the service owner or temporarily disable their subscription (if that feature exists; today it's a manual DB UPDATE).
4. If both channels are behind — not enough workers. Scale: `kubectl scale deploy/worker-default --replicas=8`.

**When to call it green:** `queue_depth` stable below 100, trending down.

---

## Alert: `DeadLetterGrowing` (`rate(notify_relay_dlq_size[1h]) > 0`)

**What it means:** messages routinely exhaust 6 retries and fall into the DLQ.

**Checklist:**
1. Which channel is growing the DLQ? — JOIN against `notify_relay_messages_total{status="dead"}` by channel.
2. **email** — usually the recipient's mail domain is bouncing (`421 too many connections`, `550 mailbox not found`). Pull the reasons from the DB:
   ```sql
   SELECT m.recipient, dl.reason, count(*)
   FROM messages m JOIN dead_letter dl ON dl.message_id = m.id
   WHERE dl.created_at > now() - interval '1 hour'
   GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
   ```
3. **webhook** — the recipient endpoint is down. Contact the owner.
4. To unstick the DLQ manually: restore the status and re-enqueue:
   ```sql
   UPDATE messages SET status='queued', updated_at=now()
   WHERE id IN (SELECT message_id FROM dead_letter WHERE created_at > now() - interval '10 minutes');
   ```
   then trigger `dispatch_scheduled` by hand (`celery -A notify_relay call tasks.scheduler.dispatch_scheduled`).

**When to call it green:** `rate(DLQ) = 0` for 15 minutes.

---

## Alert: `RateLimitSpike` (mass 429s on one API key)

**What it means:** the client overran its bucket. **Not our** bug, but worth checking.

**Checklist:**
1. Who? — find the key by `prefix` in logs: `kubectl logs -l app=api | grep '"status":429' | jq '.api_key_prefix' | sort | uniq -c`.
2. Contact the service owner. Likely causes: migration from another system (burst), retry loop on their side (bug).
3. If it's a legitimate case — raise their limit via the admin (`/admin/core/apikey/`).

---

## Alert: `OpenWebhookConnections` (>50 concurrent POSTs in a worker)

**What it means:** a slow webhook recipient is holding connections open. If there are hundreds, `requests`-style timeouts may not fire (mismatch between request timeout and OS TCP keepalive).

**Checklist:**
1. `kubectl exec worker-default -- ss -tn | grep ESTAB | wc -l`.
2. If a single IP dominates — figure out whether it's one of our recipients (DNS).
3. Worst case — restart the worker. Messages will move to retry.

---

## "What if…"

| Symptom | First suspect |
|---|---|
| `502/503` from nginx | API pod in a crash loop — `kubectl describe pod` |
| All email is `transient_error` | SMTP server is down or auth is failing |
| All webhooks are `permanent_error` 422 | `WEBHOOK_BLOCKED_NETWORKS` too strict, or DNS resolved to a private IP |
| Beat doesn't fire tasks | Either two beats running (race) or zero |
| `OpenAPI` is empty | drf-spectacular can't see the viewsets — try `manage.py spectacular --file /tmp/s.yml 2>&1` |
| `/metrics` empty | `prometheus_client` not initialized — check that the metrics middleware is imported |
| Tests fail locally but pass in CI | Most likely dependency-version skew — `pip install -e .[dev] --force-reinstall` |

---

## Escalation

| Who | When |
|---|---|
| On-call backend lead | Inability to deliver for >30 minutes, any security incident |
| Inframind / DevOps | PG / Redis infrastructure issues |
| Product | Decisions to disable a channel / recipient without their knowledge |
