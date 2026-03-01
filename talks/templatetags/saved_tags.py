"""Template filter for checking set membership."""

from typing import Any

from django import template


register = template.Library()


@register.filter
def is_in(value: Any, container: Any) -> bool:
    """
    Return True if *value* is in *container*.

    Usage::

        {% load saved_tags %}
        {{ talk.pk|is_in:saved_talk_ids }}
    """
    try:
        return value in container
    except TypeError:
        return False
