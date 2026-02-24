from __future__ import annotations

from rest_framework import serializers

from .models import Channel, Template, TemplateVersion


class TemplateVersionSerializer(serializers.ModelSerializer):
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


class TemplateReadSerializer(serializers.ModelSerializer):
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

    def get_latest_version(self, obj: Template) -> dict | None:
        v = obj.latest_version()
        if v is None:
            return None
        return TemplateVersionSerializer(v).data


class TemplateUpsertSerializer(serializers.Serializer):
    """Input schema for POST /templates/.

    If a template with the given ``(name, channel)`` exists, a new
    :class:`TemplateVersion` is appended (auto-incrementing). Otherwise the
    template row is created together with version 1.
    """

    name = serializers.CharField(max_length=128)
    channel = serializers.ChoiceField(choices=Channel.choices)
    subject_template = serializers.CharField(required=False, allow_blank=True, default="")
    body_template = serializers.CharField()
