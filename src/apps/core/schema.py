"""OpenAPI description of the ``X-API-Key`` scheme for drf-spectacular."""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension

from .authentication import API_KEY_HEADER_NAME


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "apps.core.authentication.ApiKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {"type": "apiKey", "in": "header", "name": API_KEY_HEADER_NAME}
