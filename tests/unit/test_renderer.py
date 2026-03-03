from dataclasses import dataclass

import pytest

from apps.templating.models import Channel
from apps.templating.renderer import RenderError, render


@dataclass
class _FakeVersion:
    subject_template: str
    body_template: str


def _v(body: str, subject: str = "") -> _FakeVersion:
    return _FakeVersion(subject_template=subject, body_template=body)


def test_substitutes_simple_variables() -> None:
    out = render(_v("Hello {{ name }}!"), {"name": "Alice"}, channel=Channel.EMAIL)
    assert out.body == "Hello Alice!"
    assert out.subject == ""


def test_email_subject_is_not_escaped() -> None:
    out = render(
        _v("body", subject="Re: <Order> {{ id }}"),
        {"id": "42"},
        channel=Channel.EMAIL,
    )
    assert out.subject == "Re: <Order> 42"


def test_email_body_autoescapes_html_unsafe_context() -> None:
    out = render(
        _v("Hi {{ name }}"),
        {"name": "<script>alert(1)</script>"},
        channel=Channel.EMAIL,
    )
    assert "<script>" not in out.body
    assert "&lt;script&gt;" in out.body


def test_webhook_body_does_not_escape() -> None:
    out = render(
        _v('{"id": "{{ id }}", "title": "{{ title }}"}'),
        {"id": "42", "title": "A & B"},
        channel=Channel.WEBHOOK,
    )
    assert out.body == '{"id": "42", "title": "A & B"}'


def test_missing_variable_renders_as_empty_default() -> None:
    out = render(_v("[{{ missing }}]"), {}, channel=Channel.EMAIL)
    assert out.body == "[]"


def test_render_error_wraps_template_syntax_errors() -> None:
    with pytest.raises(RenderError):
        render(_v("{% if x %}unterminated"), {"x": 1}, channel=Channel.EMAIL)


def test_filter_chain_works() -> None:
    out = render(
        _v("hello {{ name|upper }}"),
        {"name": "alice"},
        channel=Channel.EMAIL,
    )
    assert out.body == "hello ALICE"


def test_sandbox_blocks_dunder_attribute_introspection() -> None:
    # The classic "{{ x.__class__.__mro__[-1].__subclasses__() }}" escape
    # vector relies on attribute chaining. The Jinja2 sandbox returns
    # ``Undefined`` for any underscore-prefixed attribute, which renders as
    # an empty string — no class objects ever leak into the output.
    out = render(
        _v("attempt:[{{ ctx.__class__ }}][{{ ctx.__init__ }}]"),
        {"ctx": object()},
        channel=Channel.WEBHOOK,
    )
    assert out.body == "attempt:[][]"


def test_sandbox_rejects_chained_class_escape() -> None:
    # The classic CPython escape (`().__class__.__mro__[-1].__subclasses__()`)
    # tries to walk up the class hierarchy to reach arbitrary subclasses
    # (e.g. find ``os._wrap_close`` and shell out). The Jinja2 sandbox raises
    # SecurityError on ``tuple.__class__`` access — we wrap it as RenderError.
    with pytest.raises(RenderError):
        render(
            _v("{{ ().__class__.__mro__[-1].__subclasses__() }}"),
            {},
            channel=Channel.WEBHOOK,
        )
