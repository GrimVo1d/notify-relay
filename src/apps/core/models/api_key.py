from __future__ import annotations

from django.db import models


class ApiKeyQuerySet(models.QuerySet["ApiKey"]):
    def active(self) -> ApiKeyQuerySet:
        return self.filter(is_active=True, revoked_at__isnull=True)


class ApiKey(models.Model):
    name = models.CharField(max_length=128)
    prefix = models.CharField(max_length=32, db_index=True)
    hashed_secret = models.CharField(max_length=255)
    rate_limit_per_min = models.PositiveIntegerField(default=100)
    burst = models.PositiveIntegerField(default=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = ApiKeyQuerySet.as_manager()

    class Meta:
        verbose_name = "API key"
        verbose_name_plural = "API keys"
        indexes = [
            models.Index(fields=["is_active", "revoked_at"], name="api_key_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.prefix})"

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
