"""Webhook channel adapter.

Posts a signed JSON envelope to the recipient URL. The URL is re-validated
at send time and the connection goes to the address that validation
approved (the original host name is kept in ``Host`` and TLS SNI), so a
second DNS answer can't point the request somewhere internal. Proxy
environment variables are ignored and the response body is never read.
HTTP statuses are mapped to transient or permanent failure for the retry
policy.
"""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.messages_api.models import Message
from apps.messages_api.validators import validate_webhook_url

from .base import ChannelResult
from .security import (
    HMAC_HEADER_NAME,
    HMAC_TIMESTAMP_HEADER,
    MESSAGE_ID_HEADER,
    compute_signature,
)

SecretProvider = Callable[[Message], str]


class WebhookChannel:
    def __init__(self, secret_provider: SecretProvider | None = None) -> None:
        self.secret_provider: SecretProvider = secret_provider or _default_secret

    def send(self, message: Message) -> ChannelResult:
        try:
            addresses = validate_webhook_url(message.recipient)
        except ValidationError as exc:
            return ChannelResult(
                success=False, transient=False, error_message="; ".join(exc.messages)
            )

        url = httpx.URL(message.recipient)
        body = self._build_body(message)
        timestamp = str(int(time.time()))
        headers = {
            "Host": url.netloc.decode("ascii"),
            "Content-Type": "application/json",
            "User-Agent": "notify-relay/0.1",
            HMAC_HEADER_NAME: compute_signature(body, self.secret_provider(message), timestamp),
            HMAC_TIMESTAMP_HEADER: timestamp,
            MESSAGE_ID_HEADER: str(message.id),
        }
        extensions: dict[str, Any] = {}
        if url.scheme == "https" and not _is_ip_literal(url.host):
            # The A-label: the name that was resolved and is sent in ``Host``.
            extensions["sni_hostname"] = url.raw_host.decode("ascii")
        timeout = float(getattr(settings, "WEBHOOK_TIMEOUT_S", 10))

        try:
            with (
                httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout) as client,
                client.stream(
                    "POST",
                    url.copy_with(host=addresses[0]),
                    content=body,
                    headers=headers,
                    extensions=extensions,
                ) as response,
            ):
                # Only the status matters; the body is never read, so a huge or
                # endless response can't exhaust the worker's memory.
                status_code = response.status_code
        except httpx.TimeoutException as exc:
            return ChannelResult(success=False, transient=True, error_message=f"timeout: {exc}")
        except httpx.RequestError as exc:
            return ChannelResult(success=False, transient=True, error_message=str(exc))

        if 200 <= status_code < 300:
            return ChannelResult(success=True, transient=False, http_status=status_code)

        transient = status_code in {408, 425, 429} or 500 <= status_code < 600
        return ChannelResult(
            success=False,
            transient=transient,
            http_status=status_code,
            error_message=f"http {status_code}",
        )

    @staticmethod
    def _build_body(message: Message) -> bytes:
        payload: dict[str, object] = {
            "message_id": str(message.id),
            "template": (
                message.template_version.template.name
                if message.template_version_id and message.template_version
                else None
            ),
            "data": message.context or {},
            "timestamp": (message.created_at.isoformat() if message.created_at else None),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _default_secret(message: Message) -> str:
    """The sending API key's own secret, or the global one for staff-created messages."""
    if message.api_key is not None and message.api_key.webhook_secret:
        return message.api_key.webhook_secret
    return str(getattr(settings, "WEBHOOK_HMAC_SECRET", ""))


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True
