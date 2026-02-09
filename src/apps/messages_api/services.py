"""Application service: create a message, enforce idempotency, render snapshot,
schedule dispatch.

The viewset (`views.py`) is the IO boundary; this module owns the business
rules and is the place to unit-test them without spinning up DRF.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from apps.core.models import ApiKey
from apps.templating.models import Template, TemplateVersion
from apps.templating.renderer import render

from .models import Message, MessageStatus, Priority

logger = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    """Stored payload for this Idempotency-Key differs from the new one."""


class TemplateNotFound(Exception):
    """Requested template name (or version) doesn't exist or is deleted."""


def create_message(
    *,
    api_key: ApiKey | None,
    idempotency_key: str,
    validated_data: dict[str, Any],
) -> tuple[Message, bool]:
    """Return ``(message, created)``.

    ``created=False`` means the row already existed for this key, and the new
    request's payload matches the stored one. ``created=True`` means a new row
    was inserted and a dispatch task scheduled on commit.
    """
    existing = (
        Message.objects.filter(api_key=api_key, idempotency_key=idempotency_key)
        .select_related("template_version__template")
        .first()
    )
    if existing is not None:
        if _payload_matches(existing, validated_data):
            return existing, False
        raise IdempotencyConflict("Idempotency-Key reused with different payload")

    template_version = _resolve_template(validated_data)
    rendered = render(
        template_version,
        validated_data.get("context") or {},
        channel=validated_data["channel"],
    )

    with transaction.atomic():
        msg = Message.objects.create(
            idempotency_key=idempotency_key,
            api_key=api_key,
            channel=validated_data["channel"],
            recipient=validated_data["recipient"],
            template_version=template_version,
            rendered_subject=rendered.subject,
            rendered_body=rendered.body,
            context=validated_data.get("context") or {},
            priority=validated_data.get("priority", Priority.DEFAULT),
            scheduled_at=validated_data.get("scheduled_at"),
            status=MessageStatus.QUEUED,
        )
        transaction.on_commit(lambda mid=msg.id: enqueue(mid))
    return msg, True


def _resolve_template(data: dict[str, Any]) -> TemplateVersion:
    name = data.get("template")
    if not name:
        raise TemplateNotFound("template name is required")
    try:
        tmpl = Template.objects.alive().get(name=name, channel=data["channel"])
    except Template.DoesNotExist as exc:
        raise TemplateNotFound(
            f"template '{name}' not found for channel '{data['channel']}'"
        ) from exc

    requested_version = data.get("template_version")
    if requested_version:
        try:
            return tmpl.versions.get(version=requested_version)
        except TemplateVersion.DoesNotExist as exc:
            raise TemplateNotFound(
                f"version {requested_version} not found for template '{name}'"
            ) from exc

    latest = tmpl.latest_version()
    if latest is None:
        raise TemplateNotFound(f"template '{name}' has no versions")
    return latest


def _payload_matches(existing: Message, new: dict[str, Any]) -> bool:
    if existing.channel != new.get("channel"):
        return False
    if existing.recipient != new.get("recipient"):
        return False
    if (new.get("context") or {}) != (existing.context or {}):
        return False
    if new.get("scheduled_at") != existing.scheduled_at:
        return False
    new_priority = new.get("priority") or Priority.DEFAULT
    if existing.priority != new_priority:
        return False
    return True


def enqueue(message_id: str) -> None:
    """Schedule a message for delivery.

    Step-19 wires this up to the real Celery task. Until then we only log
    the intent so the rest of the pipeline (commit, response, tests) can
    work end-to-end against an in-process consumer.
    """
    logger.info("message.enqueued message_id=%s", message_id)
