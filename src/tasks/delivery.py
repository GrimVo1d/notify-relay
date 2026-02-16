"""Channel delivery tasks.

The pattern is:
    1. mark message ``sending``,
    2. invoke the channel,
    3. record a :class:`DeliveryAttempt`,
    4. on transient failure raise :class:`TransientError` so Celery's
       ``autoretry_for`` kicks in with exponential backoff + jitter;
       once ``max_retries`` is hit the message moves to ``dead`` and a
       :class:`DeadLetter` row is created,
    5. on permanent failure mark ``failed`` + create :class:`DeadLetter`
       immediately (no retries).
"""

from __future__ import annotations

import logging
from typing import Any

from celery import Task, shared_task
from django.db import transaction
from django.utils import timezone

from apps.channels.base import Channel, ChannelResult
from apps.channels.email import EmailChannel
from apps.channels.webhook import WebhookChannel
from apps.messages_api.models import (
    AttemptResult,
    DeadLetter,
    DeliveryAttempt,
    Message,
    MessageStatus,
)
from apps.templating.models import Channel as ChannelEnum

logger = logging.getLogger(__name__)

RETRY_BACKOFF_BASE_S = 30
RETRY_BACKOFF_MAX_S = 3600
MAX_RETRIES = 6


class TransientError(Exception):
    """Raised by the delivery task to trigger Celery autoretry."""


@shared_task(
    name="tasks.delivery.send_email",
    bind=True,
    autoretry_for=(TransientError,),
    retry_backoff=RETRY_BACKOFF_BASE_S,
    retry_backoff_max=RETRY_BACKOFF_MAX_S,
    retry_jitter=True,
    max_retries=MAX_RETRIES,
)
def send_email(self: Task, message_id: str) -> str:
    return _dispatch(self, message_id, EmailChannel())


@shared_task(
    name="tasks.delivery.send_webhook",
    bind=True,
    autoretry_for=(TransientError,),
    retry_backoff=RETRY_BACKOFF_BASE_S,
    retry_backoff_max=RETRY_BACKOFF_MAX_S,
    retry_jitter=True,
    max_retries=MAX_RETRIES,
)
def send_webhook(self: Task, message_id: str) -> str:
    return _dispatch(self, message_id, WebhookChannel())


def _dispatch(task: Task, message_id: str, channel: Channel) -> str:
    with transaction.atomic():
        msg = Message.objects.select_for_update().get(pk=message_id)
        if msg.status in {MessageStatus.SENT, MessageStatus.DEAD}:
            return "noop"
        msg.status = MessageStatus.SENDING
        msg.save(update_fields=["status", "updated_at"])

    started = timezone.now()
    try:
        result = channel.send(msg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("channel raised unexpectedly")
        result = ChannelResult(success=False, transient=True, error_message=repr(exc))
    finished = timezone.now()

    attempt_no = msg.attempts.count() + 1
    DeliveryAttempt.objects.create(
        message=msg,
        attempt_no=attempt_no,
        started_at=started,
        finished_at=finished,
        result=_map_result(result),
        http_status=result.http_status,
        smtp_code=result.smtp_code,
        error_message=result.error_message,
    )

    if result.success:
        msg.status = MessageStatus.SENT
        msg.save(update_fields=["status", "updated_at"])
        return "sent"

    if not result.transient:
        _move_to_dead_letter(msg, MessageStatus.FAILED, f"permanent: {result.error_message}")
        return "failed"

    retries_done = task.request.retries
    if retries_done >= MAX_RETRIES:
        _move_to_dead_letter(msg, MessageStatus.DEAD, f"exhausted: {result.error_message}")
        return "dead"

    raise TransientError(result.error_message or "transient failure")


def _move_to_dead_letter(msg: Message, status: str, reason: str) -> None:
    with transaction.atomic():
        msg.status = status
        msg.save(update_fields=["status", "updated_at"])
        DeadLetter.objects.get_or_create(
            message=msg,
            defaults={
                "reason": reason,
                "payload_snapshot": _snapshot(msg),
            },
        )


def _snapshot(msg: Message) -> dict[str, Any]:
    return {
        "id": str(msg.id),
        "channel": msg.channel,
        "recipient": msg.recipient,
        "rendered_subject": msg.rendered_subject,
        "rendered_body": msg.rendered_body,
        "context": msg.context,
        "priority": msg.priority,
    }


def _map_result(result: ChannelResult) -> str:
    if result.success:
        return AttemptResult.SUCCESS
    if result.transient:
        return AttemptResult.TRANSIENT_ERROR
    return AttemptResult.PERMANENT_ERROR


def enqueue_for(message: Message) -> None:
    """Schedule a message for delivery on the priority-matching queue."""
    task = send_email if message.channel == ChannelEnum.EMAIL else send_webhook
    task.apply_async(args=[str(message.id)], queue=message.priority)
