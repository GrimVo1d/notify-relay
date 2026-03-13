"""Celery application.

Task modules are listed explicitly in ``include``. Queue and route config
lives in Django settings under the ``CELERY_`` prefix (see ``settings/base.py``).

When ``CELERY_METRICS_PORT`` is set, the worker's main process serves
Prometheus metrics on that port, aggregated across its prefork children via
``PROMETHEUS_MULTIPROC_DIR`` (which must be set and empty at start-up).
"""

from __future__ import annotations

import os
from typing import Any

from celery import Celery
from celery.signals import worker_init, worker_process_shutdown

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notify_relay.settings.dev")

app = Celery("notify_relay", include=["tasks.delivery", "tasks.scheduler"])
app.config_from_object("django.conf:settings", namespace="CELERY")


@worker_init.connect
def _start_metrics_server(**_kwargs: Any) -> None:
    port = int(os.environ.get("CELERY_METRICS_PORT") or 0)
    if not port:
        return
    from prometheus_client import CollectorRegistry, multiprocess, start_http_server

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    start_http_server(port, registry=registry)


@worker_process_shutdown.connect
def _mark_process_dead(pid: int | None = None, **_kwargs: Any) -> None:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR") and pid is not None:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(pid)  # type: ignore[no-untyped-call]
