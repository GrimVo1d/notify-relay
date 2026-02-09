"""Rendering of stored notification templates.

Wraps :class:`django.template.Template` so we can render a stored
:class:`apps.templating.models.TemplateVersion` (or any duck-typed object with
``subject_template`` / ``body_template`` attributes) into a static snapshot
that gets persisted on the ``messages`` row and shipped to the channel.

Autoescape policy:
    * ``email`` channel templates render with autoescape **on** so user-supplied
      context values don't get interpreted as HTML.
    * ``webhook`` channel templates render with autoescape **off** since the
      body is plain text / JSON-bound; HTML escaping would mangle JSON.
    * The subject line for email is always rendered with autoescape off
      (headers are not HTML).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from django.template import Context, Template
from django.template.exceptions import TemplateSyntaxError

from .models import Channel


class _Renderable(Protocol):
    subject_template: str
    body_template: str


@dataclass(frozen=True)
class RenderedMessage:
    subject: str
    body: str


class RenderError(Exception):
    """Raised when a template fails to render. Wraps the original cause."""


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
    autoescape_body = channel == Channel.EMAIL
    try:
        body = _render_string(template_version.body_template, context, autoescape=autoescape_body)
        subject = ""
        if template_version.subject_template:
            subject = _render_string(template_version.subject_template, context, autoescape=False)
    except (TemplateSyntaxError, Exception) as exc:  # noqa: BLE001
        raise RenderError(str(exc)) from exc
    return RenderedMessage(subject=subject, body=body)


def _render_string(src: str, context: dict[str, Any], *, autoescape: bool) -> str:
    tmpl = Template(src)
    ctx = Context(context, autoescape=autoescape)
    return tmpl.render(ctx)
