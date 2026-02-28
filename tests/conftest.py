"""Shared pytest fixtures for integration tests.

We don't spin up real Postgres / Redis in this suite — the test settings
already configure SQLite + CELERY_TASK_ALWAYS_EAGER, and fixtures here
provide fakeredis for the rate-limit middleware and locmem for email.
"""

from __future__ import annotations

from collections.abc import Iterator

import fakeredis
import pytest
from django.core import mail
from django.test import override_settings
from rest_framework.test import APIClient

from apps.core.models import ApiKey
from apps.core.security import generate_api_key, hash_secret
from apps.templating.models import Channel, Template, TemplateVersion


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def with_redis(redis_client: fakeredis.FakeRedis) -> Iterator[fakeredis.FakeRedis]:
    with override_settings(RATE_LIMIT_REDIS_CLIENT_FACTORY=lambda: redis_client):
        yield redis_client


@pytest.fixture
def api_key(transactional_db) -> tuple[ApiKey, str]:
    prefix, secret, full = generate_api_key()
    ak = ApiKey.objects.create(
        name="svc-test",
        prefix=prefix,
        hashed_secret=hash_secret(secret),
        rate_limit_per_min=60,
        burst=3,
    )
    return ak, full


@pytest.fixture
def email_template(transactional_db) -> TemplateVersion:
    t = Template.objects.create(name="welcome", channel=Channel.EMAIL)
    return TemplateVersion.objects.create(
        template=t,
        version=1,
        subject_template="Hi {{ name }}",
        body_template="Hello {{ name }}",
    )


@pytest.fixture
def webhook_template(transactional_db) -> TemplateVersion:
    t = Template.objects.create(name="event", channel=Channel.WEBHOOK)
    return TemplateVersion.objects.create(
        template=t,
        version=1,
        body_template='{"event": "{{ event }}"}',
    )


@pytest.fixture
def api(api_key: tuple[ApiKey, str]) -> APIClient:
    _ak, full = api_key
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=full)
    return client


@pytest.fixture(autouse=True)
def clear_mail_outbox() -> Iterator[None]:
    mail.outbox.clear()
    yield
    mail.outbox.clear()
