"""Prometheus metrics.

Two sources, because deliveries and the API live in different processes:

* Delivery counters and the duration histogram are updated by Celery workers.
  Each worker container serves them itself (see ``notify_relay.celery``),
  aggregated across its prefork children through ``PROMETHEUS_MULTIPROC_DIR``.
* Table-shaped state (queue depth, dead-letter size) is read from the
  database when the API's ``/metrics`` endpoint is scraped, so it is always
  current and doesn't depend on which process last touched it.
"""

from __future__ import annotations

from collections.abc import Iterator

from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

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


class DatabaseStateCollector(Collector):
    """Gauges computed from the database at scrape time."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        from apps.messages_api.models import DeadLetter, Message, MessageStatus
        from apps.templating.models import Channel

        queue_depth = GaugeMetricFamily(
            "notify_relay_queue_depth",
            "Messages in queued status per channel",
            labels=["channel"],
        )
        rows = (
            Message.objects.filter(status=MessageStatus.QUEUED)
            .values("channel")
            .annotate(n=Count("id"))
        )
        depth = dict.fromkeys(Channel.values, 0)  # empty queues report 0
        depth.update({row["channel"]: row["n"] for row in rows})
        for channel, n in depth.items():
            queue_depth.add_metric([channel], n)
        yield queue_depth

        yield GaugeMetricFamily(
            "notify_relay_dlq_size",
            "Dead-letter rows count",
            value=DeadLetter.objects.count(),
        )


api_registry = CollectorRegistry(auto_describe=False)
api_registry.register(DatabaseStateCollector())


def metrics(_request: HttpRequest) -> HttpResponse:
    return HttpResponse(generate_latest(api_registry), content_type=CONTENT_TYPE_LATEST)
