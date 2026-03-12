"""DRF authentication backend that accepts ``X-API-Key: nr_<key_id>_<secret>``.

On success ``request.user`` becomes an :class:`ApiKeyPrincipal` (a stand-in
that DRF's permission checks accept) and ``request.auth`` is the matching
:class:`apps.core.models.ApiKey` row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpRequest
from rest_framework import authentication, exceptions

from .models import ApiKey
from .security import split_full_key, verify_secret

if TYPE_CHECKING:
    from rest_framework.request import Request

API_KEY_HEADER_META = "HTTP_X_API_KEY"
API_KEY_HEADER_NAME = "X-API-Key"
_RESOLVED_ATTR = "_notify_api_key"


def resolve_api_key(request: HttpRequest) -> ApiKey | None:
    """Return the active key matching the request's ``X-API-Key`` header, or ``None``.

    The result is memoised on the request, so the rate-limit middleware and
    DRF authentication share one lookup.
    """
    if hasattr(request, _RESOLVED_ATTR):
        cached: ApiKey | None = getattr(request, _RESOLVED_ATTR)
        return cached
    api_key: ApiKey | None = None
    parsed = split_full_key(request.META.get(API_KEY_HEADER_META, ""))
    if parsed is not None:
        key_id, secret = parsed
        candidate = ApiKey.objects.active().filter(key_id=key_id).first()
        if candidate is not None and verify_secret(secret, candidate.hashed_secret):
            api_key = candidate
    setattr(request, _RESOLVED_ATTR, api_key)
    return api_key


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
        if not request.META.get(API_KEY_HEADER_META):
            return None
        api_key = resolve_api_key(request._request)
        if api_key is None:
            raise exceptions.AuthenticationFailed("invalid api key")
        return ApiKeyPrincipal(api_key), api_key

    def authenticate_header(self, request: Request) -> str:
        return API_KEY_HEADER_NAME
