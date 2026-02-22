"""Prometheus metrics surface.

Counters and histograms are mutated from the hot paths (delivery tasks, API
views via middleware would also fit here later). Gauges that describe
table-shaped state (queue depth, DLQ size) are computed periodically by the
Beat-driven :func:`tasks.scheduler.refresh_metrics`.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

messages_total = Counter(
    "notify_relay_messages_total",
    "Total messages by channel and final status",
    ["channel", "status"],
)

delivery_attempts_total = Counter(
    "notify_relay_delivery_attempts_total",
    "Delivery attempts by channel and outcome",
    ["channel", "result"],
)

delivery_duration = Histogram(
    "notify_relay_delivery_duration_seconds",
    "Time spent in channel.send()",
    ["channel"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

queue_depth = Gauge(
    "notify_relay_queue_depth",
    "Messages in queued status per channel (refreshed by Beat)",
    ["channel"],
)

dlq_size = Gauge(
    "notify_relay_dlq_size",
    "Dead-letter rows count (refreshed by Beat)",
)


def metrics(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)


def refresh_gauges_from_db() -> None:
    """Populate gauges from the database.

    Called from the Beat task — kept here so the metric definitions and the
    refresher live next to each other.
    """
    from apps.messages_api.models import DeadLetter  # noqa: PLC0415

    for channel, n in _count_queued_per_channel().items():
        queue_depth.labels(channel=channel).set(n)
    dlq_size.set(DeadLetter.objects.count())


def _count_queued_per_channel() -> dict[str, int]:
    from django.db.models import Count  # noqa: PLC0415

    from apps.messages_api.models import Message, MessageStatus  # noqa: PLC0415

    rows = (
        Message.objects.filter(status=MessageStatus.QUEUED)
        .values("channel")
        .annotate(n=Count("id"))
    )
    return {r["channel"]: r["n"] for r in rows}
