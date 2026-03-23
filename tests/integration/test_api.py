"""API surface: auth rules, rate limiting, probes, metrics, webhook delivery."""

from __future__ import annotations

import json
import socket
from datetime import timedelta

import httpx
import pytest
import respx
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.channels.security import verify_signature
from apps.channels.webhook import WebhookChannel
from apps.messages_api.models import Message, MessageStatus
from tasks.scheduler import dispatch_scheduled

MISSING_MESSAGE_URL = "/api/v1/messages/01J00000000000000000000000/"

EMAIL_PAYLOAD: dict[str, object] = {
    "channel": "email",
    "recipient": "alice@example.internal",
    "template": "welcome",
    "context": {"name": "Alice"},
}


# --- auth -----------------------------------------------------------------


def test_staff_jwt_cannot_create_messages(staff_client, email_template) -> None:
    r = staff_client.post(
        "/api/v1/messages/", EMAIL_PAYLOAD, format="json", HTTP_IDEMPOTENCY_KEY="s-1"
    )
    assert r.status_code == 403
    assert r.json()["code"] == "api_key_required"


def test_malformed_api_key_is_401(transactional_db) -> None:
    client = APIClient()
    client.credentials(HTTP_X_API_KEY="nr_0123456789ab_wrong")  # gitleaks:allow
    assert client.get(MISSING_MESSAGE_URL).status_code == 401


def test_api_key_create_returns_key_and_webhook_secret(staff_client) -> None:
    r = staff_client.post("/api/v1/api-keys/", {"name": "billing"}, format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["key"].startswith(body["key_id"] + "_")
    assert len(body["webhook_secret"]) >= 32

    client = APIClient()
    client.credentials(HTTP_X_API_KEY=body["key"])
    assert client.get(MISSING_MESSAGE_URL).status_code == 404


def test_non_numeric_api_key_id_is_404(staff_client) -> None:
    assert staff_client.get("/api/v1/api-keys/abc/").status_code == 404
    assert staff_client.post("/api/v1/api-keys/abc/revoke/").status_code == 404


def test_broker_outage_still_answers_202(api, email_template, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from kombu.exceptions import OperationalError

    from apps.messages_api import services

    def broker_down(_message: Message) -> None:
        raise OperationalError("broker down")

    monkeypatch.setattr(services, "enqueue_for", broker_down)
    r = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json", HTTP_IDEMPOTENCY_KEY="b-1")
    assert r.status_code == 202, r.content
    msg = Message.objects.get(pk=r.json()["id"])
    assert msg.status == MessageStatus.QUEUED
    assert msg.enqueued_at is not None  # re-enqueued by Beat once stranded


def test_invalid_recipient_error_is_a_plain_message(api, email_template) -> None:
    payload = {**EMAIL_PAYLOAD, "recipient": "not-an-email"}
    r = api.post("/api/v1/messages/", payload, format="json", HTTP_IDEMPOTENCY_KEY="r-1")
    assert r.status_code == 422
    assert r.json()["errors"]["recipient"] == ["Enter a valid email address."]


def test_overlong_idempotency_key_is_400(api, email_template) -> None:
    r = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json", HTTP_IDEMPOTENCY_KEY="k" * 129)
    assert r.status_code == 400
    assert r.json()["code"] == "idempotency_key_too_long"


# --- templates -------------------------------------------------------------


def test_template_name_reused_for_other_channel_is_409(staff_client, email_template) -> None:
    r = staff_client.post(
        "/api/v1/templates/",
        {"name": "welcome", "channel": "webhook", "body_template": "{}"},
        format="json",
    )
    assert r.status_code == 409


def test_template_body_size_is_limited(staff_client) -> None:
    r = staff_client.post(
        "/api/v1/templates/",
        {"name": "big", "channel": "email", "body_template": "x" * (64 * 1024 + 1)},
        format="json",
    )
    assert r.status_code == 400


# --- rate limiting ---------------------------------------------------------


@override_settings(
    RATE_LIMIT_ENABLED=True, RATE_LIMIT_DEFAULT_PER_MIN=60, RATE_LIMIT_DEFAULT_BURST=2
)
def test_unknown_keys_share_the_per_ip_bucket(with_redis, transactional_db) -> None:
    codes = []
    for i in range(3):
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=f"nr_0123456789ab_guess{i}")
        codes.append(client.get("/api/v1/templates/").status_code)
    assert codes[-1] == 429
    assert codes[:2] != [429, 429]


@override_settings(RATE_LIMIT_ENABLED=True, RATE_LIMIT_AUTH_PER_MIN=60, RATE_LIMIT_AUTH_BURST=2)
def test_login_endpoint_has_its_own_strict_bucket(with_redis, transactional_db) -> None:
    client = APIClient()
    codes = [
        client.post(
            "/api/v1/auth/token/", {"username": "x", "password": "y"}, format="json"
        ).status_code
        for _ in range(3)
    ]
    assert codes == [401, 401, 429]


@override_settings(RATE_LIMIT_ENABLED=True, RATE_LIMIT_AUTH_PER_MIN=60, RATE_LIMIT_AUTH_BURST=1)
def test_per_ip_buckets_use_forwarded_for_behind_trusted_proxy(
    with_redis, transactional_db
) -> None:
    client = APIClient()

    def login(xff: str) -> int:
        return client.post(
            "/api/v1/auth/token/",
            {"username": "x", "password": "y"},
            format="json",
            HTTP_X_FORWARDED_FOR=xff,
        ).status_code

    with override_settings(NUM_PROXIES=1):
        assert login("198.51.100.1") == 401
        assert login("198.51.100.2") == 401  # another client behind the same proxy
        assert login("203.0.113.9, 198.51.100.1") == 429  # spoofed left entry is ignored
    with override_settings(NUM_PROXIES=0):
        assert login("198.51.100.3") == 401  # REMOTE_ADDR bucket, untouched so far
        assert login("198.51.100.4") == 429


@override_settings(RATE_LIMIT_ENABLED=True, RATE_LIMIT_AUTH_PER_MIN=60, RATE_LIMIT_AUTH_BURST=1)
def test_garbage_forwarded_for_falls_back_to_remote_addr(with_redis, transactional_db) -> None:
    client = APIClient()

    def login(xff: str) -> int:
        return client.post(
            "/api/v1/auth/token/",
            {"username": "x", "password": "y"},
            format="json",
            HTTP_X_FORWARDED_FOR=xff,
        ).status_code

    with override_settings(NUM_PROXIES=1):
        assert login("not-an-ip-1") == 401
        assert login("not-an-ip-2") == 429  # no fresh bucket per made-up value


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("2001:db8:1:2::1", "2001:db8:1:2:ffff::9"),  # same /64
        ("fe80::1%eth0", "fe80::1%x1"),  # zone ids dropped
        ("::ffff:198.51.100.7", "198.51.100.7"),  # IPv4-mapped folded
    ],
)
@override_settings(RATE_LIMIT_ENABLED=True, RATE_LIMIT_AUTH_PER_MIN=60, RATE_LIMIT_AUTH_BURST=1)
def test_addresses_of_one_client_share_a_bucket(
    with_redis, transactional_db, first, second
) -> None:  # type: ignore[no-untyped-def]
    client = APIClient()

    def login(xff: str) -> int:
        return client.post(
            "/api/v1/auth/token/",
            {"username": "x", "password": "y"},
            format="json",
            HTTP_X_FORWARDED_FOR=xff,
        ).status_code

    with override_settings(NUM_PROXIES=1):
        assert login(first) == 401
        assert login(second) == 429


@override_settings(RATE_LIMIT_ENABLED=True, RATE_LIMIT_DEFAULT_BURST=1)
def test_probes_and_metrics_are_not_rate_limited(with_redis, transactional_db) -> None:
    client = APIClient()
    assert [client.get("/health/live").status_code for _ in range(3)] == [200, 200, 200]
    assert [client.get("/metrics").status_code for _ in range(3)] == [200, 200, 200]


@override_settings(RATE_LIMIT_ENABLED=True)
def test_rate_limiter_fails_open_when_redis_is_down(api, email_template) -> None:
    import redis

    class DownRedis:
        def script_load(self, *_args: object) -> str:
            raise redis.ConnectionError("down")

    with override_settings(RATE_LIMIT_REDIS_CLIENT_FACTORY=DownRedis):
        r = api.post("/api/v1/messages/", EMAIL_PAYLOAD, format="json", HTTP_IDEMPOTENCY_KEY="fo-1")
    assert r.status_code == 202, r.content


# --- probes and metrics ----------------------------------------------------


def test_liveness_ignores_host_header(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/health/live", HTTP_HOST="10.1.2.3:8000")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readiness_ok(client, with_redis, transactional_db) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/health/ready", HTTP_HOST="10.1.2.3:8000")
    assert r.status_code == 200
    assert r.json()["checks"] == {"db": "ok", "redis": "ok"}


def test_readiness_failure_does_not_leak_details(client, transactional_db) -> None:  # type: ignore[no-untyped-def]
    def broken() -> object:
        raise ConnectionError("redis://user:hunter2@redis.internal:6379")

    with override_settings(RATE_LIMIT_REDIS_CLIENT_FACTORY=broken):
        r = client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["redis"] == "error"
    assert b"hunter2" not in r.content


def test_metrics_report_queue_depth_from_db(client, email_template) -> None:  # type: ignore[no-untyped-def]
    Message.objects.create(
        idempotency_key="m-1",
        channel="email",
        recipient="a@example.internal",
        template_version=email_template,
        scheduled_at=timezone.now() + timedelta(hours=1),
    )
    text = client.get("/metrics").content.decode()
    assert 'notify_relay_queue_depth{channel="email"} 1.0' in text
    assert 'notify_relay_queue_depth{channel="webhook"} 0.0' in text
    assert "notify_relay_dlq_size 0.0" in text


# --- scheduling ------------------------------------------------------------


def _post(api: APIClient, key: str, payload: dict[str, object]):  # type: ignore[no-untyped-def]
    return api.post("/api/v1/messages/", payload, format="json", HTTP_IDEMPOTENCY_KEY=key)


def test_future_scheduled_at_is_not_sent_immediately(api, email_template) -> None:
    when = (timezone.now() + timedelta(hours=1)).isoformat()
    r = _post(api, "fut-1", {**EMAIL_PAYLOAD, "scheduled_at": when})
    assert r.status_code == 202, r.content
    msg = Message.objects.get(pk=r.json()["id"])
    assert msg.status == MessageStatus.QUEUED
    assert msg.enqueued_at is None

    assert dispatch_scheduled() == 0
    Message.objects.filter(pk=msg.pk).update(scheduled_at=timezone.now() - timedelta(seconds=1))
    assert dispatch_scheduled() == 1
    msg.refresh_from_db()
    assert msg.status == MessageStatus.SENT


def test_dispatcher_reenqueues_stranded_but_not_fresh_messages(email_template) -> None:
    common = {
        "channel": "email",
        "recipient": "a@example.internal",
        "rendered_subject": "S",
        "rendered_body": "B",
        "template_version": email_template,
    }
    stranded = Message.objects.create(
        idempotency_key="str-1", enqueued_at=timezone.now() - timedelta(hours=1), **common
    )
    fresh = Message.objects.create(idempotency_key="str-2", enqueued_at=timezone.now(), **common)
    assert dispatch_scheduled() == 1
    stranded.refresh_from_db()
    fresh.refresh_from_db()
    assert stranded.status == MessageStatus.SENT
    assert fresh.status == MessageStatus.QUEUED


# --- celery wiring ---------------------------------------------------------


def test_worker_registers_all_tasks() -> None:
    from notify_relay.celery import app

    app.loader.import_default_modules()
    for name in (
        "tasks.delivery.send_email",
        "tasks.delivery.send_webhook",
        "tasks.scheduler.dispatch_scheduled",
        "tasks.scheduler.cleanup_old_messages",
    ):
        assert name in app.tasks


def test_beat_schedule_points_at_registered_tasks(settings) -> None:  # type: ignore[no-untyped-def]
    from notify_relay.celery import app

    app.loader.import_default_modules()
    for entry in settings.CELERY_BEAT_SCHEDULE.values():
        assert entry["task"] in app.tasks


# --- webhook delivery ------------------------------------------------------


@pytest.fixture
def resolve_to_public_ip(monkeypatch):  # type: ignore[no-untyped-def]
    def fake_getaddrinfo(host, port, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        assert host == "hooks.example.internal"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def test_webhook_connects_to_vetted_ip_and_signs_with_key_secret(
    api_key, webhook_template, resolve_to_public_ip
) -> None:
    ak, _full = api_key
    ak.webhook_secret = "per-key-secret"
    ak.save(update_fields=["webhook_secret"])
    msg = Message.objects.create(
        idempotency_key="wh-pin",
        api_key=ak,
        channel="webhook",
        recipient="https://hooks.example.internal/in",
        context={"event": "x"},
        template_version=webhook_template,
    )

    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://1.1.1.1/in").mock(return_value=httpx.Response(204))
        result = WebhookChannel().send(msg)

    assert result.success
    sent = route.calls.last.request
    assert sent.headers["host"] == "hooks.example.internal"
    assert sent.extensions["sni_hostname"] == "hooks.example.internal"
    assert verify_signature(
        sent.content,
        "per-key-secret",
        sent.headers["x-notify-timestamp"],
        sent.headers["x-notify-signature"],
    )
    assert json.loads(sent.content)["message_id"] == str(msg.id)


def test_webhook_idn_host_uses_one_a_label_everywhere(
    api_key, webhook_template, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    resolved: list[str] = []

    def fake_getaddrinfo(host, port, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        resolved.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    ak, _full = api_key
    msg = Message.objects.create(
        idempotency_key="wh-idn",
        api_key=ak,
        channel="webhook",
        recipient="https://faß.example.internal/in",
        template_version=webhook_template,
    )
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://1.1.1.1/in").mock(return_value=httpx.Response(204))
        assert WebhookChannel().send(msg).success

    sent = route.calls.last.request
    a_label = "xn--fa-hia.example.internal"  # IDNA 2008; IDNA 2003 would say "fass"
    assert resolved == [a_label]
    assert sent.headers["host"] == a_label
    assert sent.extensions["sni_hostname"] == a_label


@pytest.mark.parametrize(
    ("status_code", "transient"), [(301, False), (400, False), (429, True), (503, True)]
)
def test_webhook_status_mapping(api_key, webhook_template, status_code, transient) -> None:
    ak, _full = api_key
    msg = Message.objects.create(
        idempotency_key=f"wh-{status_code}",
        api_key=ak,
        channel="webhook",
        recipient="https://1.1.1.1/hook",
        template_version=webhook_template,
    )
    with respx.mock() as router:
        router.post("https://1.1.1.1/hook").mock(return_value=httpx.Response(status_code))
        result = WebhookChannel().send(msg)
    assert not result.success
    assert result.transient is transient
    assert result.error_message == f"http {status_code}"


# --- OpenAPI ---------------------------------------------------------------


def test_openapi_schema_is_valid_and_warning_free(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from django.core.management import call_command

    out = tmp_path / "schema.yaml"
    call_command("spectacular", "--validate", "--fail-on-warn", "--file", str(out))
    schema = out.read_text()
    assert "ApiKeyAuth" in schema
    assert "X-API-Key" in schema
