from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from .models import Template, TemplateVersion
from .serializers import (
    TemplateReadSerializer,
    TemplateUpsertSerializer,
    TemplateVersionSerializer,
)


class TemplateViewSet(viewsets.GenericViewSet):
    """Templates and their versions.

    POST   /api/v1/templates/                        — create template or
                                                       append a new version
    GET    /api/v1/templates/                        — list alive templates
    GET    /api/v1/templates/{name}/                 — detail with latest
    DELETE /api/v1/templates/{name}/                 — soft delete
    GET    /api/v1/templates/{name}/versions/        — list versions
    GET    /api/v1/templates/{name}/versions/{n}/    — single version
    """

    permission_classes = [permissions.IsAdminUser]
    queryset = Template.objects.alive()
    serializer_class = TemplateReadSerializer
    lookup_field = "name"
    lookup_value_regex = r"[\w.-]+"

    def list(self, request: Request) -> Response:
        qs = self.queryset.order_by("name")
        data = [TemplateReadSerializer(t).data for t in qs]
        return Response(data)

    def retrieve(self, request: Request, name: str | None = None) -> Response:
        tmpl = get_object_or_404(self.queryset, name=name)
        return Response(TemplateReadSerializer(tmpl).data)

    def create(self, request: Request) -> Response:
        ser = TemplateUpsertSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_400_BAD_REQUEST)
        data = ser.validated_data

        with transaction.atomic():
            tmpl = (
                Template.objects.alive().filter(name=data["name"], channel=data["channel"]).first()
            )
            if tmpl is None:
                tmpl = Template.objects.create(
                    name=data["name"], channel=data["channel"], is_active=True
                )
                next_version = 1
            else:
                latest = tmpl.latest_version()
                next_version = (latest.version if latest else 0) + 1

            version = TemplateVersion.objects.create(
                template=tmpl,
                version=next_version,
                subject_template=data.get("subject_template", ""),
                body_template=data["body_template"],
                created_by=request.user if request.user.is_authenticated else None,
            )

        body = TemplateReadSerializer(tmpl).data
        body["created_version"] = TemplateVersionSerializer(version).data
        return Response(body, status=status.HTTP_201_CREATED)

    def destroy(self, request: Request, name: str | None = None) -> Response:
        tmpl = get_object_or_404(self.queryset, name=name)
        tmpl.deleted_at = timezone.now()
        tmpl.save(update_fields=["deleted_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="versions")
    def versions(self, request: Request, name: str | None = None) -> Response:
        tmpl = get_object_or_404(self.queryset, name=name)
        qs = tmpl.versions.order_by("-version")
        return Response([TemplateVersionSerializer(v).data for v in qs])

    @action(
        detail=True,
        methods=["get"],
        url_path=r"versions/(?P<version>\d+)",
    )
    def version_detail(
        self, request: Request, name: str | None = None, version: str | None = None
    ) -> Response:
        tmpl = get_object_or_404(self.queryset, name=name)
        v = get_object_or_404(tmpl.versions, version=int(version or 0))
        return Response(TemplateVersionSerializer(v).data)
