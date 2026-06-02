"""Room creation helpers - single lookup and batch bulk-create."""

from typing import TYPE_CHECKING

from pytanis.pretalx.models import State

from talks.management.commands._pretalx.submission import SubmissionData
from talks.management.commands._pretalx.types import VerbosityLevel
from talks.models import Room


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytanis.pretalx.models import Submission

    from talks.management.commands._pretalx.context import ImportContext


# ------------------------------------------------------------------
# Single room helper
# ------------------------------------------------------------------


def get_or_create_room(
    room_name: str,
    ctx: ImportContext,
    *,
    pretalx_id: int | None = None,
) -> Room | None:
    """
    Return the event-scoped :class:`~talks.models.Room` for *room_name*, creating it if needed.

    Rooms are scoped to ``ctx.event_obj`` and matched by the stable ``(event, pretalx_id)``
    first, then ``(event, name)``. A room renamed on Pretalx is renamed IN PLACE (same row,
    so its streamings / slido_link / capacity and all Talk FKs stay attached) and a legacy
    row's ``pretalx_id`` is stamped on first sight. Returns ``None`` when *room_name* is empty.

    In ``--dry-run`` and ``--detect-only`` modes nothing is written: the matched row is
    returned UNCHANGED (no rename/stamp - the rename is surfaced as a reviewable diff
    instead) or, for a brand-new room, an unsaved instance is returned.
    """
    if not room_name:
        return None

    existing = Room.resolve_for_event(
        event=ctx.event_obj,
        pretalx_id=pretalx_id,
        name=room_name,
    )
    if existing is not None:
        if not (ctx.dry_run or ctx.detect_only):
            _reconcile_existing_room(existing, room_name, pretalx_id, ctx)
        return existing

    # Not found. Neither dry-run nor detect-only may write to the Room table; return an
    # unsaved stand-in so the diff builder can still reference the room.
    if ctx.dry_run or ctx.detect_only:
        ctx.log(
            f"Would create room: {room_name} ({'dry run' if ctx.dry_run else 'detect-only'})",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        return Room(event=ctx.event_obj, pretalx_id=pretalx_id, name=room_name, description="")

    room = Room.objects.create(
        event=ctx.event_obj,
        pretalx_id=pretalx_id,
        name=room_name,
        description=f"Room imported from Pretalx: {room_name}",
    )
    ctx.log(
        f"Created room: {room_name}",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )
    return room


def _reconcile_existing_room(
    room: Room,
    name: str,
    pretalx_id: int | None,
    ctx: ImportContext,
) -> None:
    """
    Bring an existing room in line with Pretalx: rename in place and/or stamp the id.

    Write-path only (callers must guard on ``not (dry_run or detect_only)``). Renaming
    keeps the same row so streamings/talks/config stay attached.

    Stamping the id is collision-free without an extra check: we only reach the stamp
    branch when ``resolve_for_event`` matched by name, which only happens after its
    ``(event, pretalx_id)`` lookup missed - i.e. no other room in this event holds the id.
    """
    update_fields: list[str] = []
    if room.name != name:
        ctx.log(
            f"Renaming room {room.name!r} -> {name!r} (matched by Pretalx id {pretalx_id})",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        room.name = name
        update_fields.append("name")
    if pretalx_id is not None and room.pretalx_id is None:
        room.pretalx_id = pretalx_id
        update_fields.append("pretalx_id")
    elif pretalx_id is not None and room.pretalx_id != pretalx_id:
        ctx.log(
            f"Room {room.name!r} matched by name but its Pretalx id "
            f"({room.pretalx_id}) differs from {pretalx_id}; leaving id unchanged",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
    if update_fields:
        room.save(update_fields=update_fields)


# ------------------------------------------------------------------
# Batch room creation
# ------------------------------------------------------------------


def batch_create_rooms(
    submissions: Sequence[Submission],
    ctx: ImportContext,
) -> None:
    """
    Bulk-create rooms referenced by confirmed/accepted *submissions*, scoped to the event.

    Rooms already present in ``ctx.event_obj`` (matched by name) are skipped. Names are
    deduped within the batch (one row per name per event), keeping the first-seen Pretalx
    id. Per-talk ``get_or_create_room`` later stamps ids and renames as needed; this is
    just a fast pre-create so the per-talk path mostly hits existing rows.
    """
    # name -> first-seen pretalx id for this event's confirmed/accepted submissions.
    pairs: dict[str, int | None] = {}
    for submission in submissions:
        if submission.state not in (State.confirmed, State.accepted):
            continue
        data = SubmissionData(submission, ctx.pretalx_event_url)
        if data.room:
            pairs.setdefault(data.room, data.pretalx_room_id)

    if not pairs:
        return

    event = ctx.event_obj
    existing_names = set(
        Room.objects.filter(event=event, name__in=pairs).values_list("name", flat=True),
    )
    rooms_to_create = [
        Room(
            event=event,
            pretalx_id=pretalx_id,
            name=name,
            description=f"Room imported from Pretalx: {name}",
        )
        for name, pretalx_id in pairs.items()
        if name not in existing_names
    ]

    if rooms_to_create:
        Room.objects.bulk_create(rooms_to_create, ignore_conflicts=True)
        ctx.log(
            f"Batch created {len(rooms_to_create)} rooms",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
