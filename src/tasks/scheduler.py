"""Beat-driven housekeeping tasks.

* :func:`dispatch_scheduled` releases messages whose ``scheduled_at`` has
  arrived. Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` on PostgreSQL so
  multiple beat-runners or concurrent workers can claim batches without
  contention. SQLite (used in tests) falls back to plain ``select_for_update``.
* :func:`cleanup_old_messages` deletes terminal messages older than the
  retention window in fixed-size batches to avoid one giant DELETE.
* :func:`refresh_metrics` is a Beat-friendly hook — populated in step 24
  once the Prometheus registry has gauges to update.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db import connection, transaction
from django.utils import timezone

from apps.messages_api.models import Message, MessageStatus
from tasks.delivery import enqueue_for

logger = logging.getLogger(__name__)

DISPATCH_BATCH_SIZE = 200
CLEANUP_AGE_DAYS = 90
CLEANUP_BATCH_SIZE = 1000
_TERMINAL_STATUSES = (MessageStatus.SENT, MessageStatus.FAILED, MessageStatus.DEAD)


@shared_task(name="tasks.scheduler.dispatch_scheduled")
def dispatch_scheduled() -> int:
    """Enqueue messages whose ``scheduled_at`` has elapsed. Returns count."""
    now = timezone.now()
    with transaction.atomic():
        qs = Message.objects.filter(
            status=MessageStatus.QUEUED,
            scheduled_at__lte=now,
        ).order_by("scheduled_at")
        if connection.features.has_select_for_update_skip_locked:
            qs = qs.select_for_update(skip_locked=True)
        else:
            qs = qs.select_for_update()
        batch = list(qs[:DISPATCH_BATCH_SIZE])
        for msg in batch:
            enqueue_for(msg)
    if batch:
        logger.info("dispatch_scheduled enqueued=%s", len(batch))
    return len(batch)


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


@shared_task(name="tasks.scheduler.refresh_metrics")
def refresh_metrics() -> int:
    """Refresh Prometheus gauges. Step-24 owns the actual gauge updates."""
    return 0
