"""
Reusable logic for the Talk.event backfill (data migration 0028).

Kept out of the migration file so it can be unit-tested; the migration calls it with
historical model classes via ``apps.get_model``. It only touches fields present in both
the historical and current Talk/Event models (``event``, the Talk->Room FK, and the
already-required ``Room.event``), so it is safe to run from a migration.
"""

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


def backfill_talk_events(
    *,
    talk_model: Any,
    event_model: Any,
    log: Callable[[str], None] | None = None,
) -> None:
    """
    Assign ``Talk.event`` for every talk that still has none.

    Rooms are event-scoped by the time this runs (migration 0027), so a talk with a room
    inherits that room's event (always coherent). A talk with no room falls back to the
    newest event, when any exists; otherwise it is left unassigned (degenerate DB with
    talks but no events). Idempotent: only ``event IS NULL`` talks are touched.
    """
    emit = log or (lambda _msg: None)
    fallback = event_model.objects.order_by("-year", "-pk").first()

    for talk in talk_model.objects.filter(event__isnull=True).select_related("room"):
        if talk.room_id is not None and talk.room.event_id is not None:
            talk.event_id = talk.room.event_id
            talk.save(update_fields=["event"])
        elif fallback is not None:
            talk.event = fallback
            talk.save(update_fields=["event"])
            emit(
                f"Talk {talk.pk} '{talk.title}' has no room; "
                f"assigned to fallback event '{fallback.slug}'.",
            )
        else:
            emit(
                f"Talk {talk.pk} '{talk.title}' has no room and no event exists; left unassigned.",
            )
