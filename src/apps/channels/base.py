from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from apps.messages_api.models import Message


@dataclass(frozen=True)
class ChannelResult:
    success: bool
    transient: bool  # only meaningful when success=False
    error_message: str = ""
    http_status: int | None = None
    smtp_code: int | None = None


class Channel(Protocol):
    def send(self, message: Message) -> ChannelResult: ...
