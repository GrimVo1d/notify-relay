"""Test profile: in-process stack (SQLite, eager Celery, locmem mail)."""

from .base import *
from .base import SIMPLE_JWT

DEBUG = False
ALLOWED_HOSTS = ["testserver"]

SECRET_KEY = "test-insecure-secret-key"
JWT_SIGNING_KEY = "test-insecure-jwt-signing-key-0123456789"
API_KEY_HASH_PEPPER = "test-insecure-pepper"
WEBHOOK_HMAC_SECRET = "test-insecure-webhook-secret"
SIMPLE_JWT = {**SIMPLE_JWT, "SIGNING_KEY": JWT_SIGNING_KEY}

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

LOGGING_CONFIG = None
LOGGING = {}

RATE_LIMIT_ENABLED = False
