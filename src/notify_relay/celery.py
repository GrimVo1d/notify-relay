"""Celery application factory.

Auto-discovers task modules under the ``tasks`` package. Queue/route config
lives in Django settings under the ``CELERY_`` prefix (see ``settings/base.py``).
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "notify_relay.settings.dev")

app = Celery("notify_relay")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(["tasks"])
