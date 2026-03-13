"""Beat-driven housekeeping tasks.

* :func:`dispatch_scheduled` enqueues ``queued`` messages whose ``scheduled_at``
  has arrived, and re-enqueues ``queued`` messages that were handed to the
  broker long ago but never picked up (lost on a broker restart). Rows are
  claimed with ``SELECT ... FOR UPDATE SKIP LOCKED`` on PostgreSQL, stamped
  with ``enqueued_at`` and only sent to the broker after the commit.
* :func:`cleanup_old_messages` deletes terminal messages older than the
  retention window in fixed-size batches to avoid one giant DELETE.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.messages_api.models import Message, MessageStatus
from tasks.delivery import enqueue_for

logger = logging.getLogger(__name__)

DISPATCH_BATCH_SIZE = 200
REENQUEUE_AFTER = timedelta(minutes=15)
CLEANUP_AGE_DAYS = 90
CLEANUP_BATCH_SIZE = 1000
_TERMINAL_STATUSES = (MessageStatus.SENT, MessageStatus.FAILED, MessageStatus.DEAD)


@shared_task(name="tasks.scheduler.dispatch_scheduled")
def dispatch_scheduled() -> int:
    """Enqueue due and stranded messages. Returns how many were enqueued."""
    now = timezone.now()
    due = Q(enqueued_at__isnull=True, scheduled_at__lte=now)
    stranded = Q(enqueued_at__lt=now - REENQUEUE_AFTER)
    with transaction.atomic():
        qs = Message.objects.filter(due | stranded, status=MessageStatus.QUEUED).order_by(
            "scheduled_at", "created_at"
        )
        if connection.features.has_select_for_update_skip_locked:
            qs = qs.select_for_update(skip_locked=True)
        else:
            qs = qs.select_for_update()
        batch = list(qs[:DISPATCH_BATCH_SIZE])
        Message.objects.filter(pk__in=[m.pk for m in batch]).update(enqueued_at=now)
        transaction.on_commit(lambda: _enqueue_all(batch))
    if batch:
        logger.info("dispatch_scheduled enqueued=%s", len(batch))
    return len(batch)


def _enqueue_all(messages: list[Message]) -> None:
    for message in messages:
        enqueue_for(message)


@shared_task(name="tasks.scheduler.cleanup_old_messages")
def cleanup_old_messages() -> int:
    """Delete terminal-state messages older than the retention window.

    Walks the table in ``CLEANUP_BATCH_SIZE`` chunks so individual DELETE
    statements don't lock the table for too long.
    """
    cutoff = timezone.now() - timedelta(days=CLEANUP_AGE_DAYS)
    base_qs = Message.objects.filter(
        status__in=_TERMINAL_STATUSES,
        created_at__lt=cutoff,
    )

    total_deleted = 0
    while True:
        ids = list(base_qs.values_list("pk", flat=True)[:CLEANUP_BATCH_SIZE])
        if not ids:
            break
        deleted, _ = Message.objects.filter(pk__in=ids).delete()
        total_deleted += deleted
        if len(ids) < CLEANUP_BATCH_SIZE:
            break

    if total_deleted:
        logger.info("cleanup_old_messages deleted=%s", total_deleted)
    return total_deleted
