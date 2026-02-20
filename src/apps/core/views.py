from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .models import ApiKey
from .security import generate_api_key, hash_secret
from .serializers import ApiKeyCreateSerializer, ApiKeyReadSerializer


class ApiKeyViewSet(viewsets.GenericViewSet):
    """API-key management. Staff-only.

    POST /api/v1/api-keys/             — create; full key returned ONCE
    GET  /api/v1/api-keys/             — list active + revoked
    GET  /api/v1/api-keys/{id}/        — detail
    POST /api/v1/api-keys/{id}/revoke/ — revoke (soft-delete)
    """

    permission_classes = [permissions.IsAdminUser]
    queryset = ApiKey.objects.all().order_by("-created_at")
    serializer_class = ApiKeyReadSerializer

    def list(self, request: Request) -> Response:
        data = [ApiKeyReadSerializer(k).data for k in self.queryset]
        return Response(data)

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        ak = get_object_or_404(self.queryset, pk=pk)
        return Response(ApiKeyReadSerializer(ak).data)

    def create(self, request: Request) -> Response:
        ser = ApiKeyCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        data = ser.validated_data

        prefix, secret, full = generate_api_key()
        ak = ApiKey.objects.create(
            name=data["name"],
            prefix=prefix,
            hashed_secret=hash_secret(secret),
            rate_limit_per_min=data["rate_limit_per_min"],
            burst=data["burst"],
            is_active=True,
        )
        body = ApiKeyReadSerializer(ak).data
        body["key"] = full  # shown once, then irretrievable
        return Response(body, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def revoke(self, request: Request, pk: str | None = None) -> Response:
        ak = get_object_or_404(self.queryset, pk=pk)
        if ak.is_revoked:
            return Response({"detail": "already revoked"}, status=status.HTTP_409_CONFLICT)
        ak.is_active = False
        ak.revoked_at = timezone.now()
        ak.save(update_fields=["is_active", "revoked_at"])
        return Response(ApiKeyReadSerializer(ak).data)
