"""DRF authentication backend that accepts ``X-API-Key: <prefix><secret>``.

On success ``request.user`` becomes an :class:`ApiKeyPrincipal` (a stand-in
that DRF's permission checks accept) and ``request.auth`` is the matching
:class:`apps.core.models.ApiKey` row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import authentication, exceptions

from .models import ApiKey
from .security import split_full_key, verify_secret

if TYPE_CHECKING:
    from rest_framework.request import Request

API_KEY_HEADER_META = "HTTP_X_API_KEY"
API_KEY_HEADER_NAME = "X-API-Key"


class ApiKeyPrincipal:
    """Lightweight user-like wrapper around an :class:`ApiKey`.

    DRF only needs ``is_authenticated`` to be truthy for permission classes
    like ``IsAuthenticated``. We expose the underlying :class:`ApiKey` via
    :attr:`api_key` so downstream code can scope queries by it.
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, api_key: ApiKey) -> None:
        self.api_key = api_key
        self.pk = api_key.pk
        self.username = api_key.name

    def __str__(self) -> str:
        return f"api_key:{self.api_key.name}"

    def __repr__(self) -> str:
        return f"<ApiKeyPrincipal {self.api_key.name}#{self.api_key.pk}>"


class ApiKeyAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request: Request) -> tuple[ApiKeyPrincipal, ApiKey] | None:
        raw = request.META.get(API_KEY_HEADER_META)
        if not raw:
            return None

        parsed = split_full_key(raw)
        if parsed is None:
            raise exceptions.AuthenticationFailed("malformed api key")
        prefix, secret = parsed

        candidates = list(ApiKey.objects.active().filter(prefix=prefix))
        for ak in candidates:
            if verify_secret(secret, ak.hashed_secret):
                return ApiKeyPrincipal(ak), ak

        raise exceptions.AuthenticationFailed("invalid api key")

    def authenticate_header(self, request: Request) -> str:
        return API_KEY_HEADER_NAME
