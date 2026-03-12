"""Local development profile: throwaway secrets, DEBUG on unless DEBUG=0."""

from .base import *
from .base import (
    ALLOWED_HOSTS,
    API_KEY_HASH_PEPPER,
    JWT_SIGNING_KEY,
    SECRET_KEY,
    SIMPLE_JWT,
    WEBHOOK_HMAC_SECRET,
    env,
)

DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = ALLOWED_HOSTS or ["localhost", "127.0.0.1", "[::1]"]

SECRET_KEY = SECRET_KEY or "dev-insecure-secret-key-change-me"
JWT_SIGNING_KEY = JWT_SIGNING_KEY or "dev-insecure-jwt-signing-key-change-me"
API_KEY_HASH_PEPPER = API_KEY_HASH_PEPPER or "dev-insecure-pepper"
WEBHOOK_HMAC_SECRET = WEBHOOK_HMAC_SECRET or "dev-insecure-webhook-secret"
SIMPLE_JWT = {**SIMPLE_JWT, "SIGNING_KEY": JWT_SIGNING_KEY}
