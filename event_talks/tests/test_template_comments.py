"""
Guards against Django template comments leaking into rendered pages.

``{# ... #}`` is a *single-line* comment: Django's lexer matches it with ``{#.*?#}`` and ``.``
does not match a newline, so a comment spanning two lines is never recognized as a token and is
emitted as literal text. The page still renders, nothing errors, and the prose ends up on screen
for visitors to read - which is exactly what happened on this branch, in eleven files.

``{% comment %} ... {% endcomment %}`` is the multi-line form and is stripped at compile time.
"""

import re
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.template import engines
from django.urls import reverse


if TYPE_CHECKING:
    from django.test import Client


TEMPLATE_ROOT = Path(settings.BASE_DIR) / "templates"


def _multiline_short_comments(text: str) -> list[int]:
    """Return the 1-based line numbers of ``{# ... #}`` comments whose ``#}`` is on a later line."""
    broken = []
    for match in re.finditer(r"\{#", text):
        line_end = text.find("\n", match.start())
        rest_of_line = text[match.start() : line_end if line_end != -1 else len(text)]
        if "#}" not in rest_of_line:
            broken.append(text.count("\n", 0, match.start()) + 1)
    return broken


def test_the_short_comment_syntax_really_is_single_line_only() -> None:
    """
    Pin the behavior the rest of this module depends on.

    If a future Django made ``{# #}`` multi-line, the scan below would be pointless rather than
    wrong, and this test says so first.
    """
    engine = engines["django"]
    assert engine.from_string("A{# one line #}B").render({}) == "AB"
    assert engine.from_string("A{% comment %}one\ntwo{% endcomment %}B").render({}) == "AB"
    # The broken case: emitted verbatim instead of being stripped.
    assert engine.from_string("A{# one\ntwo #}B").render({}) != "AB"


@pytest.mark.parametrize(
    "template_path",
    sorted(TEMPLATE_ROOT.rglob("*.html")),
    ids=lambda p: str(p.relative_to(TEMPLATE_ROOT)),
)
def test_no_template_uses_a_multiline_short_comment(template_path: Path) -> None:
    """Every explanatory comment that spans lines must use ``{% comment %}``."""
    broken = _multiline_short_comments(template_path.read_text(encoding="utf-8"))

    assert not broken, (
        f"{template_path.relative_to(TEMPLATE_ROOT)} has a multi-line {{# #}} comment on line(s) "
        f"{broken}, which renders as visible text. Use {{% comment %}} ... {{% endcomment %}}."
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["home", "talk_list", "schedule"])
def test_rendered_pages_contain_no_comment_delimiters(client: Client, url_name: str) -> None:
    """
    End to end: the delimiters themselves must not appear in any response body.

    The scan above catches the mistake in the source, but this catches it wherever it comes from,
    including a template this suite does not otherwise render.
    """
    response = client.get(reverse(url_name))

    assert response.status_code == HTTPStatus.OK
    body = response.content.decode()
    assert "{#" not in body
    assert "#}" not in body
    assert "{% comment %}" not in body
