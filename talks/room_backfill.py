"""
Reusable logic for the Room.event backfill (data migration 0025).

Kept out of the migration file so it can be unit-tested against the real models,
while the migration calls it with historical model classes via ``apps.get_model``.
It only touches fields present in both the historical and current Room/Talk/Event
models (``event``, the Talk->Room FK, ``Event.year``/``slug``), so it is safe to run
from a migration.
"""

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable


def backfill_room_events(
    *,
    room_model: Any,
    event_model: Any,
    talk_model: Any,
    log: Callable[[str], None] | None = None,
) -> None:
    """
    Assign ``Room.event`` for every room that still has none, derived from its talks.

    Rules (rooms are expected to be per-event):
    - All of a room's talks belong to one event -> assign that event.
    - A room with no talks (or only talks that have no event) -> the newest event, when
      any event exists; otherwise left unassigned (degenerate DB: rooms but no events).
    - A room whose talks span MORE than one event -> ``RuntimeError``: fail loud rather
      than silently mis-assign (cloning shared rooms is intentionally out of scope).

    Idempotent: only rooms with ``event IS NULL`` are touched, so re-running is a no-op.
    """
    emit = log or (lambda _msg: None)
    # A room with no talks has nothing to derive an event from; pick the newest event as a
    # deterministic, logged fallback so the later null=False migration has no NULLs left.
    fallback = event_model.objects.order_by("-year", "-pk").first()
    cross_event: list[tuple[Any, str, list[int]]] = []

    for room in room_model.objects.filter(event__isnull=True):
        event_ids = list(
            talk_model.objects.filter(room=room, event__isnull=False)
            .values_list("event", flat=True)
            .distinct(),
        )
        if len(event_ids) == 1:
            room.event_id = event_ids[0]
            room.save(update_fields=["event"])
        elif not event_ids:
            if fallback is not None:
                room.event = fallback
                room.save(update_fields=["event"])
                emit(
                    f"Room {room.pk} '{room.name}' has no talks; "
                    f"assigned to fallback event '{fallback.slug}'.",
                )
            else:
                emit(
                    f"Room {room.pk} '{room.name}' has no talks and no event exists; "
                    "left unassigned.",
                )
        else:
            cross_event.append((room.pk, room.name, event_ids))

    if cross_event:
        msg = (
            "Cannot backfill Room.event: the following rooms have talks spanning multiple "
            "events (rooms are expected to be per-event). Split them manually in admin, then "
            f"re-run the migration. Offenders (pk, name, event_ids): {cross_event!r}"
        )
        raise RuntimeError(msg)
