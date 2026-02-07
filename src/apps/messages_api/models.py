from __future__ import annotations

from django.db import models
from ulid import ULID

from apps.core.models import ApiKey
from apps.templating.models import Channel, TemplateVersion


def _new_ulid() -> str:
    return str(ULID())


class Priority(models.TextChoices):
    HIGH = "high", "High"
    DEFAULT = "default", "Default"
    LOW = "low", "Low"


class MessageStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    SENDING = "sending", "Sending"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    DEAD = "dead", "Dead"


class AttemptResult(models.TextChoices):
    SUCCESS = "success", "Success"
    TRANSIENT_ERROR = "transient_error", "Transient error"
    PERMANENT_ERROR = "permanent_error", "Permanent error"


class Message(models.Model):
    id = models.CharField(primary_key=True, max_length=26, default=_new_ulid, editable=False)
    idempotency_key = models.CharField(max_length=128)
    api_key = models.ForeignKey(
        ApiKey,
        on_delete=models.PROTECT,
        related_name="messages",
        null=True,
        blank=True,
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    recipient = models.TextField()
    template_version = models.ForeignKey(
        TemplateVersion,
        on_delete=models.PROTECT,
        related_name="messages",
        null=True,
        blank=True,
    )
    rendered_subject = models.TextField(blank=True, default="")
    rendered_body = models.TextField()
    context = models.JSONField(default=dict, blank=True)
    priority = models.CharField(
        max_length=16, choices=Priority.choices, default=Priority.DEFAULT
    )
    status = models.CharField(
        max_length=16, choices=MessageStatus.choices, default=MessageStatus.QUEUED
    )
    scheduled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["api_key", "idempotency_key"],
                name="message_idempotency_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "scheduled_at"], name="message_status_sched_idx"),
            models.Index(fields=["created_at"], name="message_created_idx"),
            models.Index(fields=["recipient"], name="message_recipient_idx"),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Message({self.id}, {self.channel}, {self.status})"


class DeliveryAttempt(models.Model):
    id = models.BigAutoField(primary_key=True)
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    attempt_no = models.PositiveIntegerField()
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=24, choices=AttemptResult.choices)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    smtp_code = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "attempt_no"],
                name="delivery_attempt_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["message", "attempt_no"], name="delivery_attempt_msg_idx"),
        ]

    def __str__(self) -> str:
        return f"attempt#{self.attempt_no} ({self.result}) of {self.message_id}"


class DeadLetter(models.Model):
    id = models.BigAutoField(primary_key=True)
    message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        related_name="dead_letter",
    )
    reason = models.TextField()
    payload_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_at"], name="dead_letter_created_idx"),
        ]

    def __str__(self) -> str:
        return f"DeadLetter({self.message_id}): {self.reason[:60]}"
