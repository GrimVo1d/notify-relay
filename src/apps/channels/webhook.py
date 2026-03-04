"""Webhook channel adapter.

Posts a signed JSON envelope to the recipient URL. Re-validates the URL at
send time (defense in depth against SSRF — values that passed validation
at API time may resolve differently now). Maps HTTP responses to transient
or permanent failure for the retry policy.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.messages_api.models import Message
from apps.messages_api.validators import validate_webhook_url

from .base import ChannelResult
from .security import (
    HMAC_HEADER_NAME,
    HMAC_TIMESTAMP_HEADER,
    MESSAGE_ID_HEADER,
    compute_signature,
)

logger = logging.getLogger(__name__)

SecretProvider = Callable[[Message], str]


class WebhookChannel:
    def __init__(self, secret_provider: SecretProvider | None = None) -> None:
        self.secret_provider: SecretProvider = secret_provider or _default_secret

    def send(self, message: Message) -> ChannelResult:
        try:
            validate_webhook_url(message.recipient)
        except ValidationError as exc:
            return ChannelResult(success=False, transient=False, error_message=str(exc))

        body = self._build_body(message)
        secret = self.secret_provider(message)
        timestamp = timezone.now().isoformat()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "notify-relay/0.1",
            HMAC_HEADER_NAME: compute_signature(body, secret),
            HMAC_TIMESTAMP_HEADER: timestamp,
            MESSAGE_ID_HEADER: str(message.id),
        }
        timeout = float(getattr(settings, "WEBHOOK_TIMEOUT_S", 10))

        try:
            response = httpx.post(
                message.recipient,
                content=body,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            return ChannelResult(success=False, transient=True, error_message=f"timeout: {exc}")
        except httpx.RequestError as exc:
            return ChannelResult(success=False, transient=True, error_message=str(exc))

        status_code = response.status_code
        if 200 <= status_code < 300:
            return ChannelResult(success=True, transient=False, http_status=status_code)

        transient = status_code in {408, 425, 429} or 500 <= status_code < 600
        return ChannelResult(
            success=False,
            transient=transient,
            http_status=status_code,
            error_message=f"http {status_code}: {response.text[:200]}",
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
    return str(getattr(settings, "WEBHOOK_HMAC_SECRET", ""))
