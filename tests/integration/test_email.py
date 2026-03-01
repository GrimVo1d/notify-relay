"""Integration test for the email channel against Django's locmem backend.

A full Mailpit-against-the-real-SMTP exercise lives in the broader e2e
suite (see ``tests/integration/test_e2e.py``). This one only checks that
the channel translates a stored Message into a properly addressed
EmailMessage and surfaces SMTP errors as transient/permanent correctly.
"""

from __future__ import annotations

import smtplib
from unittest.mock import patch

import pytest
from django.core import mail

from apps.channels.email import EmailChannel
from apps.messages_api.models import Message


@pytest.fixture
def message(db) -> Message:
    return Message.objects.create(
        idempotency_key="t-1",
        api_key=None,
        channel="email",
        recipient="bob@example.com",
        rendered_subject="Hi Bob",
        rendered_body="Hello there",
        priority="default",
    )


def test_send_success_records_in_locmem(message: Message) -> None:
    result = EmailChannel().send(message)
    assert result.success is True
    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["bob@example.com"]
    assert sent.subject == "Hi Bob"
    assert sent.body == "Hello there"


def test_empty_subject_falls_back_to_placeholder(message: Message) -> None:
    message.rendered_subject = ""
    message.save()
    EmailChannel().send(message)
    assert mail.outbox[0].subject == "(no subject)"


def test_smtp_4xx_is_transient(message: Message) -> None:
    exc = smtplib.SMTPResponseException(421, b"service not available")
    with patch.object(mail.EmailMessage, "send", side_effect=exc):
        result = EmailChannel().send(message)
    assert result.success is False
    assert result.transient is True
    assert result.smtp_code == 421


def test_smtp_5xx_is_permanent(message: Message) -> None:
    exc = smtplib.SMTPResponseException(550, b"no such mailbox")
    with patch.object(mail.EmailMessage, "send", side_effect=exc):
        result = EmailChannel().send(message)
    assert result.success is False
    assert result.transient is False
    assert result.smtp_code == 550


def test_generic_smtp_exception_is_transient(message: Message) -> None:
    with patch.object(mail.EmailMessage, "send", side_effect=smtplib.SMTPServerDisconnected("bye")):
        result = EmailChannel().send(message)
    assert result.success is False
    assert result.transient is True


def test_zero_sent_is_transient_error(message: Message) -> None:
    with patch.object(mail.EmailMessage, "send", return_value=0):
        result = EmailChannel().send(message)
    assert result.success is False
    assert result.transient is True
