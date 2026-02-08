from __future__ import annotations

from django.conf import settings
from django.db import models


class Channel(models.TextChoices):
    EMAIL = "email", "Email"
    WEBHOOK = "webhook", "Webhook"


class TemplateQuerySet(models.QuerySet["Template"]):
    def alive(self) -> TemplateQuerySet:
        return self.filter(deleted_at__isnull=True)

    def active(self) -> TemplateQuerySet:
        return self.alive().filter(is_active=True)


class Template(models.Model):
    name = models.CharField(max_length=128)
    channel = models.CharField(max_length=16, choices=Channel.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = TemplateQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=models.Q(deleted_at__isnull=True),
                name="template_name_unique_alive",
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "is_active"], name="template_channel_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.channel}]"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def latest_version(self) -> TemplateVersion | None:
        return self.versions.order_by("-version").first()


class TemplateVersion(models.Model):
    template = models.ForeignKey(
        Template,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version = models.PositiveIntegerField()
    subject_template = models.TextField(blank=True, default="")
    body_template = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["template", "version"],
                name="template_version_unique",
            ),
        ]
        ordering = ["template_id", "-version"]

    def __str__(self) -> str:
        return f"{self.template.name} v{self.version}"
