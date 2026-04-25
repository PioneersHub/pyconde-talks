"""Utilities for the talks app."""

from typing import TYPE_CHECKING

from .models import Talk


if TYPE_CHECKING:
    from users.models import CustomUser


def get_talk_by_id_or_pretalx(talk_id: str, *, user: CustomUser | None = None) -> Talk | None:
    """
    Return a Talk by primary key or Pretalx ID.

    Try to interpret `talk_id` as the model primary key. If that fails or no Talk exists with that
    pk, fall back to checking the `pretalx_link`.

    When *user* is provided the queryset is scoped to talks the user may access, preventing
    cross-event information disclosure (a 302 vs 404 reveals whether a talk exists).
    """
    qs = Talk.objects.accessible_to(user) if user else Talk.objects.all()

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
