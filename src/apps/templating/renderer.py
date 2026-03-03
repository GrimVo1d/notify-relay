"""Rendering of stored notification templates with Jinja2.

We use :class:`jinja2.sandbox.SandboxedEnvironment` (not the default ``Environment``)
because template bodies come from user-controlled rows in the DB; a sandboxed
env blocks access to dunder-attributes (``__class__``, ``__mro__``, ``__globals__``)
and unsafe operations, so a malicious template can't break out into the host
runtime.

Autoescape policy:
    * ``email`` channel → autoescape **on** (treat body as HTML; user-supplied
      context can't be interpreted as markup).
    * ``webhook`` channel → autoescape **off** (body is plain text or JSON;
      escaping would mangle JSON encoding).
    * Email subject lines are always rendered with autoescape off — they're
      headers, not HTML.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from jinja2 import ChainableUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .models import Channel


class _Renderable(Protocol):
    subject_template: str
    body_template: str


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    body: str


class RenderError(Exception):
    """Raised when a template fails to parse or render. Wraps the original cause."""


_env_escaped = SandboxedEnvironment(
    autoescape=True,
    undefined=ChainableUndefined,
    keep_trailing_newline=True,
)
_env_raw = SandboxedEnvironment(
    autoescape=False,
    undefined=ChainableUndefined,
    keep_trailing_newline=True,
)


def render(
    template_version: _Renderable,
    context: dict[str, Any],
    *,
    channel: str,
) -> RenderedMessage:
    """Render ``template_version`` against ``context`` for the given ``channel``.

    The returned ``RenderedMessage`` is the persisted snapshot — the channel
    adapter must not re-render this content downstream.
    """
    body_env = _env_escaped if channel == Channel.EMAIL else _env_raw

    try:
        body = body_env.from_string(template_version.body_template).render(context)
        subject = ""
        if template_version.subject_template:
            subject = _env_raw.from_string(template_version.subject_template).render(context)
    except TemplateError as exc:
        raise RenderError(str(exc)) from exc

    return RenderedMessage(subject=subject, body=body)
