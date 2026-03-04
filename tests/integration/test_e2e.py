"""End-to-end coverage of SPEC §10 scenarios.

The suite exercises the API → service → Celery task → channel → state
machine round-trip in-process. Real Celery workers, Postgres, Mailpit and
network calls live in step-27's CI matrix.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import httpx
import respx
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels.base import ChannelResult
from apps.channels.email import EmailChannel
from apps.messages_api.models import DeadLetter, Message, MessageStatus
from tasks.scheduler import dispatch_scheduled

EMAIL_PAYLOAD: dict[str, object] = {
    "channel": "email",
    "recipient": "alice@example.com",
    "template": "welcome",
    "context": {"name": "Alice"},
}


def _idem(api: APIClient, key: str) -> None:
    api.credentials(
        HTTP_X_API_KEY=api._credentials["HTTP_X_API_KEY"],
        HTTP_IDEMPOTENCY_KEY=key,
    )


def test_idempotency_repeat_same_payload_returns_same_id(api, email_template) -> None:
    _idem(api, "k1")
    r1 = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
    assert r1.status_code == 202, r1.content
    r2 = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
    assert r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    assert Message.objects.filter(idempotency_key="k1").count() == 1


def test_idempotency_conflict_on_different_payload(api, email_template) -> None:
    _idem(api, "k2")
    r1 = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
    assert r1.status_code == 202
    diff = {**EMAIL_PAYLOAD, "recipient": "eve@example.com"}
    r2 = api.post("/api/v1/messages/", diff, format="json")
    assert r2.status_code == 409


def test_rate_limit_returns_429_with_retry_after(api, email_template, with_redis) -> None:
    with override_settings(RATE_LIMIT_ENABLED=True):
        for i in range(3):
            _idem(api, f"rl-{i}")
            payload = {**EMAIL_PAYLOAD, "recipient": f"u{i}@example.com"}
            r = api.post("/api/v1/messages/", payload, format="json")
            assert r.status_code == 202, (i, r.status_code)
        _idem(api, "rl-3")
        r = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
        assert r.status_code == 429
        assert int(r["Retry-After"]) >= 1


def test_webhook_url_in_private_subnet_is_rejected(api, webhook_template) -> None:
    _idem(api, "ssrf-1")
    payload = {"channel": "webhook", "recipient": "http://127.0.0.1/x", "template": "event"}
    r = api.post("/api/v1/messages/", payload, format="json")
    assert r.status_code == 422


def test_webhook_retry_then_success(api_key, webhook_template) -> None:
    # Drive _dispatch manually to exercise the transient-then-success path.
    # Celery's eager mode doesn't actually loop on retry, so we simulate the
    # two attempts by calling the task body twice with task.request.retries
    # incremented.
    from unittest.mock import MagicMock

    from apps.channels.webhook import WebhookChannel
    from tasks.delivery import TransientError, _dispatch

    ak, _full = api_key
    url = "https://1.1.1.1/hook"
    msg = Message.objects.create(
        idempotency_key="wh-retry",
        api_key=ak,
        channel="webhook",
        recipient=url,
        rendered_body='{"event":"x"}',
        template_version=webhook_template,
    )

    with respx.mock() as router:
        router.post(url).mock(side_effect=[httpx.Response(503), httpx.Response(200)])

        fake_task = MagicMock()
        fake_task.request.retries = 0
        try:
            _dispatch(fake_task, str(msg.id), WebhookChannel())
        except TransientError:
            pass

        fake_task.request.retries = 1
        assert _dispatch(fake_task, str(msg.id), WebhookChannel()) == "sent"

    msg.refresh_from_db()
    assert msg.status == MessageStatus.SENT
    statuses = list(msg.attempts.order_by("attempt_no").values_list("result", flat=True))
    assert statuses == ["transient_error", "success"]


def test_dead_letter_after_max_retries(api_key, email_template) -> None:
    from unittest.mock import MagicMock

    from tasks.delivery import MAX_RETRIES, _dispatch

    ak, _full = api_key
    msg = Message.objects.create(
        idempotency_key="dlq-1",
        api_key=ak,
        channel="email",
        recipient="x@example.com",
        rendered_subject="S",
        rendered_body="B",
        template_version=email_template,
    )
    transient = ChannelResult(success=False, transient=True, error_message="boom", smtp_code=421)
    fake_task = MagicMock()
    fake_task.request.retries = MAX_RETRIES  # already at the last attempt
    with patch.object(EmailChannel, "send", return_value=transient):
        assert _dispatch(fake_task, str(msg.id), EmailChannel()) == "dead"
    msg.refresh_from_db()
    assert msg.status == MessageStatus.DEAD
    assert DeadLetter.objects.filter(message=msg).exists()
    assert msg.dead_letter.reason.startswith("exhausted:")


def test_scheduled_dispatch_picks_due_messages(api, email_template) -> None:
    Message.objects.create(
        idempotency_key="sch-1",
        api_key=None,
        channel="email",
        recipient="a@example.com",
        rendered_subject="S",
        rendered_body="B",
        template_version=email_template,
        scheduled_at=timezone.now() - timedelta(minutes=1),
    )
    Message.objects.create(
        idempotency_key="sch-2",
        api_key=None,
        channel="email",
        recipient="b@example.com",
        rendered_subject="S",
        rendered_body="B",
        template_version=email_template,
        scheduled_at=timezone.now() + timedelta(hours=2),
    )
    assert dispatch_scheduled() == 1
    assert Message.objects.get(idempotency_key="sch-1").status == MessageStatus.SENT
    assert Message.objects.get(idempotency_key="sch-2").status == MessageStatus.QUEUED


def test_message_get_is_scoped_to_owner_api_key(api, api_key, email_template) -> None:
    _idem(api, "scope-1")
    r = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
    mid = r.json()["id"]

    from apps.core.models import ApiKey
    from apps.core.security import generate_api_key, hash_secret

    p2, s2, full2 = generate_api_key()
    ApiKey.objects.create(name="other", prefix=p2, hashed_secret=hash_secret(s2))
    other = APIClient()
    other.credentials(HTTP_X_API_KEY=full2)
    assert other.get(f"/api/v1/messages/{mid}/").status_code == 404


def test_missing_idempotency_header_is_rejected(api, email_template) -> None:
    r = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
    assert r.status_code == 400


def test_unknown_template_returns_422(api) -> None:
    _idem(api, "nt-1")
    payload = {"channel": "email", "recipient": "a@b.com", "template": "nope"}
    r = api.post("/api/v1/messages/", payload, format="json")
    assert r.status_code == 422


def test_email_actually_lands_in_locmem(api, email_template) -> None:
    _idem(api, "lm-1")
    api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["alice@example.com"]
    assert sent.subject == "Hi Alice"
    assert sent.body == "Hello Alice"


def test_template_version_pinning(api, email_template) -> None:
    from apps.templating.models import TemplateVersion

    TemplateVersion.objects.create(
        template=email_template.template,
        version=2,
        subject_template="V2 {{ name }}",
        body_template="v2 body {{ name }}",
    )
    _idem(api, "vp-1")
    api.post(
        "/api/v1/messages/",
        {**EMAIL_PAYLOAD, "context": {"name": "Z"}, "recipient": "u@e.com"},
        format="json",
    )
    assert mail.outbox[-1].body == "v2 body Z"

    _idem(api, "vp-2")
    api.post(
        "/api/v1/messages/",
        {**EMAIL_PAYLOAD, "context": {"name": "Z"}, "recipient": "u@e.com", "template_version": 1},
        format="json",
    )
    assert mail.outbox[-1].body == "Hello Z"


def test_revoked_api_key_cannot_authenticate(api, api_key, email_template) -> None:
    ak, _full = api_key
    ak.is_active = False
    ak.revoked_at = timezone.now()
    ak.save(update_fields=["is_active", "revoked_at"])
    _idem(api, "rev-1")
    r = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json")
    assert r.status_code in (401, 403)
