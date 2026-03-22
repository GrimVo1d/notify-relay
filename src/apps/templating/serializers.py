from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import Channel, Template, TemplateVersion


class TemplateVersionSerializer(serializers.ModelSerializer[TemplateVersion]):
    class Meta:
        model = TemplateVersion
        fields = [
            "id",
            "version",
            "subject_template",
            "body_template",
            "created_at",
        ]
        read_only_fields = ["id", "version", "created_at"]


class TemplateReadSerializer(serializers.ModelSerializer[Template]):
    latest_version = serializers.SerializerMethodField()

    class Meta:
        model = Template
        fields = [
            "id",
            "name",
            "channel",
            "is_active",
            "created_at",
            "latest_version",
        ]
        read_only_fields = fields

    def get_latest_version(self, obj: Template) -> dict[str, Any] | None:
        v = obj.latest_version()
        if v is None:
            return None
        return TemplateVersionSerializer(v).data


class TemplateUpsertSerializer(serializers.Serializer[Any]):
    """Input schema for POST /templates/.

    Template names are unique. If ``name`` exists for the same channel, a new
    :class:`TemplateVersion` is appended (auto-incrementing); if it exists for
    another channel the view answers 409. Otherwise the template row is
    created together with version 1.
    """

    name = serializers.CharField(max_length=128)
    channel = serializers.ChoiceField(choices=Channel.choices)
    subject_template = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=998
    )
    body_template = serializers.CharField(max_length=64 * 1024)
