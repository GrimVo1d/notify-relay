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
            return Response(
                {"detail": f"{IDEMPOTENCY_HEADER_NAME} header is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = MessageCreateSerializer(data=request.data)
        if not ser.is_valid():
            return Response(ser.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        api_key = request.auth if isinstance(request.auth, ApiKey) else None

        try:
            msg, _created = create_message(
                api_key=api_key,
                idempotency_key=idempotency_key,
                validated_data=dict(ser.validated_data),
            )
        except IdempotencyConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except TemplateNotFound as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except RenderError as exc:
            return Response(
                {"detail": f"template render failed: {exc}"},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        return Response(MessageReadSerializer(msg).data, status=status.HTTP_202_ACCEPTED)

    def retrieve(self, request: Request, *args, **kwargs) -> Response:
        msg = get_object_or_404(self.get_queryset(), pk=kwargs["pk"])
        return Response(MessageReadSerializer(msg).data)
