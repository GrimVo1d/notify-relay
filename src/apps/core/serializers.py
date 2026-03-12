from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import ApiKey


class ApiKeyReadSerializer(serializers.ModelSerializer[ApiKey]):
    is_revoked = serializers.BooleanField(read_only=True)

    class Meta:
        model = ApiKey
        fields = [
            "id",
            "name",
            "key_id",
            "rate_limit_per_min",
            "burst",
            "is_active",
            "is_revoked",
            "created_at",
            "revoked_at",
        ]
        read_only_fields = fields


class ApiKeyCreateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=128)
    rate_limit_per_min = serializers.IntegerField(min_value=1, default=100)
    burst = serializers.IntegerField(min_value=1, default=200)
