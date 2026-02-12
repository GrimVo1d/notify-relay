from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.templating.models import Channel

from .models import Message, MessageStatus, Priority
from .validators import validate_recipient


class MessageCreateSerializer(serializers.Serializer):
    """Input schema for ``POST /api/v1/messages``.

    The viewset is responsible for resolving ``template`` (a name) to a
    :class:`apps.templating.models.TemplateVersion` row. We only validate
    shape and channel-specific recipient form here.
    """

    channel = serializers.ChoiceField(choices=Channel.choices)
    recipient = serializers.CharField(max_length=2048)
    template = serializers.CharField(required=False, allow_blank=False)
    template_version = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    context = serializers.JSONField(required=False, default=dict)
    priority = serializers.ChoiceField(choices=Priority.choices, default=Priority.DEFAULT)
    scheduled_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_context(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise serializers.ValidationError("context must be a JSON object")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_recipient(attrs["channel"], attrs["recipient"])
        except Exception as exc:
            raise serializers.ValidationError({"recipient": str(exc)}) from exc
        return attrs


class MessageReadSerializer(serializers.ModelSerializer):
    template_name = serializers.SerializerMethodField()
    template_version = serializers.IntegerField(source="template_version.version", read_only=True)
    status = serializers.ChoiceField(choices=MessageStatus.choices, read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "idempotency_key",
            "channel",
            "recipient",
            "template_name",
            "template_version",
            "rendered_subject",
            "rendered_body",
            "context",
            "priority",
            "status",
            "scheduled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_template_name(self, obj: Message) -> str | None:
        if obj.template_version_id and obj.template_version:
            return obj.template_version.template.name
        return None
