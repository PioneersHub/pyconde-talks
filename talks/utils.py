"""Utilities for the talks app."""

from datetime import date, datetime
from typing import TYPE_CHECKING

from .models import Talk


if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser
    from django.http import HttpRequest


def is_htmx_request(request: HttpRequest) -> bool:
    """Return True when *request* was issued by HTMX."""
    return request.headers.get("HX-Request") == "true"


def parse_iso_date(value: str | None) -> date | None:
    """
    Parse a ``YYYY-MM-DD`` string into a date, returning None on empty or malformed input.

    Shared by the talk-list, schedule, and chair views so they all reject bad ?date params the same
    way (previously each had its own copy, one of which caught a different exception set).
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError, TypeError:
        return None


def get_talk_by_id_or_pretalx(
    talk_id: str,
    *,
    user: AbstractBaseUser | AnonymousUser | None = None,
) -> Talk | None:
    """
    Return a Talk by primary key or Pretalx ID.

    Try to interpret `talk_id` as the model primary key. If that fails or no Talk exists with that
    pk, fall back to checking the `pretalx_link`.

    The queryset is always scoped through ``accessible_to``, including when *user* is ``None``
    (treated as anonymous), which prevents cross-event information disclosure: a 302 rather than a
    404 would reveal that a talk exists. There is deliberately no unscoped branch, so a caller that
    wants every talk has to reach for ``Talk.objects`` and say so.
    """
    qs = Talk.objects.accessible_to(user)

    # Try to interpret as primary key
    try:
        pk = int(talk_id)
    except TypeError, ValueError:
        pk = None

    if pk is not None:
        talk = qs.filter(pk=pk).first()
        if talk:
            return talk

    # Fallback: try pretalx_id embedded in the pretalx_link
    return qs.filter(pretalx_link__contains=f"/talk/{talk_id}").first()
