"""Template filter to highlight matched search terms using the mark element."""

import re

from django import template
from django.utils.html import format_html


register = template.Library()


def _compile_pattern(terms: list[str]) -> re.Pattern[str] | None:
    words = [t for t in (term.strip() for term in terms) if t]
    if not words:
        return None
    escaped = [re.escape(w) for w in words]
    pattern = r"(" + "|".join(escaped) + r")"
    return re.compile(pattern, flags=re.IGNORECASE)


@register.filter(name="highlight")
def highlight(text: str, query: str | None) -> str:
    """
    Wrap query terms in the mark element with a Tailwind-friendly class.

    Usage: {{ value|highlight:search_query }}
    """
    if not text or not query:
        return text or ""
    pattern = _compile_pattern(query.split())
    if not pattern:
        return text

    try:
        # Build a safe string by interleaving escaped text and formatted highlights
        parts: list[str] = []
        args: list[str] = []
        last = 0
        for m in pattern.finditer(text):
            parts.append("{}")
            parts.append("{}")
            args.append(text[last : m.start()])
            args.append(
                format_html(
                    '<mark class="bg-yellow-200 text-black rounded px-0.5">{}</mark>',
                    m.group(0),
                ),
            )
            last = m.end()
        parts.append("{}")
        args.append(text[last:])
        highlight_template = "".join(parts)
        return format_html(highlight_template, *args)
    except re.error:
        return text
