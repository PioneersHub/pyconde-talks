"""Utilities for the talks app."""

from .models import Talk


def get_talk_by_id_or_pretalx(talk_id: str) -> Talk | None:
    """
    Return a Talk by primary key or Pretalx ID.

    Try to interpret `talk_id` as the model primary key. If that fails or no Talk exists with that
    pk, fall back to checking the `pretalx_link`.
    """
    # Try to interpret as primary key
    try:
        pk = int(talk_id)
    except (TypeError, ValueError):  # fmt: skip
        pk = None

    if pk is not None:
        talk = Talk.objects.filter(pk=pk).first()
        if talk:
            return talk

    # Fallback: try pretalx_id embedded in the pretalx_link
    return Talk.objects.filter(pretalx_link__contains=f"/talk/{talk_id}").first()
