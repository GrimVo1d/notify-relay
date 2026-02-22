from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core.models import ApiKey
from apps.templating.renderer import RenderError

from .models import Message
from .serializers import MessageCreateSerializer, MessageReadSerializer
from .services import IdempotencyConflict, TemplateNotFound, create_message

IDEMPOTENCY_HEADER_META = "HTTP_IDEMPOTENCY_KEY"
IDEMPOTENCY_HEADER_NAME = "Idempotency-Key"


def _error(code: str, message: str, http_status: int) -> Response:
    """Standard 4xx response shape: ``{"code": "...", "detail": "..."}``.

    Clients should branch on ``code`` (machine-readable) rather than ``detail``
    (human-readable, may change).
    """
    return Response({"code": code, "detail": message}, status=http_status)


class MessageViewSet(viewsets.GenericViewSet):
    """API for transactional messages.

    POST /api/v1/messages/             — enqueue a message (202)
    GET  /api/v1/messages/{id}/        — retrieve current state
    """

    permission_classes = [IsAuthenticated]
    serializer_class = MessageReadSerializer
    lookup_value_regex = r"[A-Za-z0-9]{26}"
    queryset = Message.objects.select_related(
        "api_key", "template_version", "template_version__template"
    )

    def get_queryset(self):  # type: ignore[override]
        qs = super().get_queryset()
        auth = getattr(self.request, "auth", None)
        if isinstance(auth, ApiKey):
            return qs.filter(api_key=auth)
        user = self.request.user
        if user.is_authenticated and getattr(user, "is_staff", False):
            return qs
        return qs.none()

    def create(self, request: Request, *args, **kwargs) -> Response:
        idempotency_key = (request.META.get(IDEMPOTENCY_HEADER_META) or "").strip()
        if not idempotency_key:
            return _error(
                "idempotency_key_missing",
                f"{IDEMPOTENCY_HEADER_NAME} header is required",
                status.HTTP_400_BAD_REQUEST,
            )

        ser = MessageCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(
                {"code": "validation_error", "errors": ser.errors},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        api_key = request.auth if isinstance(request.auth, ApiKey) else None

        try:
            msg, _created = create_message(
                api_key=api_key,
                idempotency_key=idempotency_key,
                validated_data=dict(ser.validated_data),
            )
        except IdempotencyConflict as exc:
            return _error("idempotency_conflict", str(exc), status.HTTP_409_CONFLICT)
        except TemplateNotFound as exc:
            return _error("template_not_found", str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
        except RenderError as exc:
            return _error(
                "template_render_failed",
                f"template render failed: {exc}",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(MessageReadSerializer(msg).data, status=status.HTTP_202_ACCEPTED)

    def retrieve(self, request: Request, *args, **kwargs) -> Response:
        msg = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])
        return Response(MessageReadSerializer(msg).data)
