"""Email channel adapter.

Uses Django's email backend (configurable via ``EMAIL_*`` settings). The
backend is in turn driven by ``smtplib``, which surfaces server responses as
:class:`smtplib.SMTPResponseException`. We map SMTP 4xx → transient
(worth retrying) and 5xx → permanent (give up).
"""

from __future__ import annotations

import smtplib

from django.conf import settings
from django.core.mail import EmailMessage

from apps.messages_api.models import Message

from .base import ChannelResult


class EmailChannel:
    def send(self, message: Message) -> ChannelResult:
        email = EmailMessage(
            subject=message.rendered_subject or "(no subject)",
            body=message.rendered_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[message.recipient],
        )
        try:
            sent = email.send(fail_silently=False)
        except smtplib.SMTPResponseException as exc:
            code = int(exc.smtp_code)
            return ChannelResult(
                success=False,
                transient=400 <= code < 500,
                smtp_code=code,
                error_message=(
                    exc.smtp_error.decode("utf-8", "replace")
                    if isinstance(exc.smtp_error, bytes)
                    else str(exc.smtp_error)
                ),
            )
        except smtplib.SMTPException as exc:
            return ChannelResult(success=False, transient=True, error_message=str(exc))

        if sent == 0:
            return ChannelResult(
                success=False,
                transient=True,
                error_message="email backend reported zero sent",
            )
        return ChannelResult(success=True, transient=False)
