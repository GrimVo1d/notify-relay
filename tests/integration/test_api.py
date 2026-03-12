"""API surface: auth rules, rate limiting, probes, metrics, webhook delivery."""

from __future__ import annotations

import json
import socket

import httpx
import pytest
import respx
from django.test import override_settings
from rest_framework.test import APIClient

from apps.channels.security import verify_signature
from apps.channels.webhook import WebhookChannel
from apps.messages_api.models import Message

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
    assert client.get(MISSING_MESSAGE_URL).status_code in (401, 403)


def test_api_key_create_returns_key_and_webhook_secret(staff_client) -> None:
    r = staff_client.post("/api/v1/api-keys/", {"name": "billing"}, format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["key"].startswith(body["key_id"] + "_")
    assert len(body["webhook_secret"]) >= 32

    client = APIClient()
    client.credentials(HTTP_X_API_KEY=body["key"])
    assert client.get(MISSING_MESSAGE_URL).status_code == 404


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
