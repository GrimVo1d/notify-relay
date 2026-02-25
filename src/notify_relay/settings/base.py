"""Base Django settings shared by dev / test / prod profiles.

Environment variables are read via django-environ. See `.env.example` at the
repo root for the canonical list and defaults.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[3]
SRC_DIR = BASE_DIR / "src"

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "dev-insecure-change-me"),
    ALLOWED_HOSTS=(list, ["*"]),
    DATABASE_URL=(str, f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
    REDIS_URL=(str, "redis://localhost:6379/0"),
    CELERY_BROKER_URL=(str, "redis://localhost:6379/1"),
    CELERY_RESULT_BACKEND=(str, "redis://localhost:6379/2"),
    LOG_LEVEL=(str, "INFO"),
    LOG_FORMAT=(str, "plain"),
    DEFAULT_FROM_EMAIL=(str, "no-reply@notify-relay.local"),
    SMTP_HOST=(str, "localhost"),
    SMTP_PORT=(int, 1025),
    SMTP_USER=(str, ""),
    SMTP_PASSWORD=(str, ""),
    SMTP_TLS=(bool, False),
    API_KEY_HASH_PEPPER=(str, "dev-insecure-pepper"),
    JWT_SIGNING_KEY=(str, "dev-insecure-jwt"),
    WEBHOOK_TIMEOUT_S=(int, 10),
    WEBHOOK_HMAC_SECRET=(str, "dev-insecure-webhook-secret"),
    WEBHOOK_BLOCKED_NETWORKS=(
        list,
        [
            "127.0.0.0/8",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "169.254.0.0/16",
            "::1/128",
            "fc00::/7",
        ],
    ),
)

env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "apps.core",
    "apps.templating",
    "apps.messages_api",
    "apps.ratelimit",
    "apps.channels",
]

MIDDLEWARE = [
    "apps.core.middleware.RequestIDMiddleware",
    "apps.core.middleware.RateLimitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "notify_relay.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "notify_relay.wsgi.application"
ASGI_APPLICATION = "notify_relay.asgi.application"

DATABASES = {"default": env.db()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.core.authentication.ApiKeyAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

SIMPLE_JWT = {
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY"),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "notify-relay API",
    "DESCRIPTION": (
        "Transactional notification relay: HTTP API for queuing email and "
        "outbound webhook deliveries with templates, idempotency, retries, "
        "rate limiting and dead-letter handling."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "TAGS": [
        {"name": "messages", "description": "Queue and inspect notifications"},
        {"name": "templates", "description": "Message templates and versions"},
        {"name": "api-keys", "description": "Service API key management (staff only)"},
        {"name": "auth", "description": "JWT obtain / refresh"},
    ],
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "ENUM_NAME_OVERRIDES": {
        "ChannelEnum": "apps.templating.models.Channel",
        "MessageStatusEnum": "apps.messages_api.models.MessageStatus",
        "PriorityEnum": "apps.messages_api.models.Priority",
    },
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_TIME_LIMIT = 60
CELERY_TASK_SOFT_TIME_LIMIT = 50

from kombu import Queue  # noqa: E402

CELERY_TASK_QUEUES = (
    Queue("high"),
    Queue("default"),
    Queue("low"),
)
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_DEFAULT_ROUTING_KEY = "default"

CELERY_TASK_ROUTES = {
    "tasks.delivery.send_email": {"queue": "default"},
    "tasks.delivery.send_webhook": {"queue": "default"},
    "tasks.scheduler.dispatch_scheduled": {"queue": "default"},
    "tasks.scheduler.cleanup_old_messages": {"queue": "low"},
    "tasks.scheduler.refresh_metrics": {"queue": "low"},
}

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    "dispatch-scheduled": {
        "task": "tasks.scheduler.dispatch_scheduled",
        "schedule": 60.0,
    },
    "cleanup-old-messages": {
        "task": "tasks.scheduler.cleanup_old_messages",
        "schedule": crontab(hour="3", minute="0"),
    },
    "refresh-metrics": {
        "task": "tasks.scheduler.refresh_metrics",
        "schedule": 30.0,
    },
}

EMAIL_HOST = env("SMTP_HOST")
EMAIL_PORT = env("SMTP_PORT")
EMAIL_HOST_USER = env("SMTP_USER")
EMAIL_HOST_PASSWORD = env("SMTP_PASSWORD")
EMAIL_USE_TLS = env("SMTP_TLS")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")

API_KEY_HASH_PEPPER = env("API_KEY_HASH_PEPPER")
JWT_SIGNING_KEY = env("JWT_SIGNING_KEY")
WEBHOOK_TIMEOUT_S = env("WEBHOOK_TIMEOUT_S")
WEBHOOK_BLOCKED_NETWORKS = env("WEBHOOK_BLOCKED_NETWORKS")
WEBHOOK_HMAC_SECRET = env("WEBHOOK_HMAC_SECRET")

RATE_LIMIT_ENABLED = env.bool("RATE_LIMIT_ENABLED", default=True)
RATE_LIMIT_DEFAULT_PER_MIN = env.int("RATE_LIMIT_DEFAULT_PER_MIN", default=100)
RATE_LIMIT_DEFAULT_BURST = env.int("RATE_LIMIT_DEFAULT_BURST", default=200)
RATE_LIMIT_REDIS_CLIENT_FACTORY: object | None = None

LOG_LEVEL = env("LOG_LEVEL")
LOG_FORMAT = env("LOG_FORMAT")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
        "json": {
            "()": "apps.core.logging.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT if LOG_FORMAT in {"plain", "json"} else "plain",
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "celery": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}
