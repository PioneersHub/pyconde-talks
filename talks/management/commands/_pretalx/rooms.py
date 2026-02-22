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
) -> Room | None:
    """
    Return the :class:`~talks.models.Room` for *room_name*, creating it if needed.

    Returns ``None`` when *room_name* is empty.  In ``--dry-run`` mode an
    unsaved instance is returned so callers can still reference a room.
    """
    if not room_name:
        return None

    existing = Room.objects.filter(name=room_name).first()
    if existing:
        ctx.log(
            f"Using existing room: {room_name}",
            VerbosityLevel.DETAILED,
        )
        return existing

    if ctx.dry_run:
        ctx.log(
            f"Would create room: {room_name} (dry run)",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        return Room(name=room_name, description="")

    room = Room.objects.create(
        name=room_name,
        description=f"Room imported from Pretalx: {room_name}",
    )
    ctx.log(
        f"Created room: {room_name}",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )
    return room


# ------------------------------------------------------------------
# Batch room creation
# ------------------------------------------------------------------


def batch_create_rooms(
    submissions: Sequence[Submission],
    ctx: ImportContext,
) -> None:
    """
    Bulk-create all rooms referenced by confirmed/accepted *submissions*.

    Rooms that already exist in the database are silently skipped.
    """
    room_names: set[str] = set()
    for submission in submissions:
        if submission.state not in (State.confirmed, State.accepted):
            continue
        data = SubmissionData(submission, ctx.pretalx_event_url)
        if data.room:
            room_names.add(data.room)

    if not room_names:
        return

    existing_rooms = set(
        Room.objects.filter(name__in=room_names).values_list("name", flat=True),
    )
    rooms_to_create = room_names - existing_rooms

    if rooms_to_create:
        Room.objects.bulk_create(
            [
                Room(name=name, description=f"Room imported from Pretalx: {name}")
                for name in rooms_to_create
            ],
            ignore_conflicts=True,
        )
        ctx.log(
            f"Batch created {len(rooms_to_create)} rooms",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
