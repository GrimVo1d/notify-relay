"""Production profile: every secret and ALLOWED_HOSTS must come from the environment."""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import (
    ALLOWED_HOSTS,
    API_KEY_HASH_PEPPER,
    JWT_SIGNING_KEY,
    LOGGING,
    SECRET_KEY,
    SPECTACULAR_SETTINGS,
    WEBHOOK_HMAC_SECRET,
    env,
)

_MIN_SECRET_LENGTH = 32

for _name, _value in {
    "SECRET_KEY": SECRET_KEY,
    "JWT_SIGNING_KEY": JWT_SIGNING_KEY,
    "API_KEY_HASH_PEPPER": API_KEY_HASH_PEPPER,
    "WEBHOOK_HMAC_SECRET": WEBHOOK_HMAC_SECRET,
}.items():
    if len(_value) < _MIN_SECRET_LENGTH or _value.startswith(("dev-", "ci-", "test-")):
        raise ImproperlyConfigured(
            f"{_name} must be set to a random value of at least {_MIN_SECRET_LENGTH} characters"
        )

if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must list the public host names explicitly")

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
# Probes and Prometheus talk plain HTTP to the container.
SECURE_REDIRECT_EXEMPT = [r"^health/", r"^metrics$"]
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

LOG_FORMAT = "json"
LOGGING["handlers"]["console"]["formatter"] = "json"

# The OpenAPI schema and docs UI are for operators only.
SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
}
