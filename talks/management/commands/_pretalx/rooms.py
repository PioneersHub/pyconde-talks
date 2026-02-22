"""Batch room creation to reduce database round-trips."""

from typing import TYPE_CHECKING, Any

from pytanis.pretalx.models import State

from talks.management.commands._pretalx.submission import SubmissionData
from talks.management.commands._pretalx.types import VerbosityLevel
from talks.models import Room


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytanis.pretalx.models import Submission


def batch_create_rooms(
    event_slug: str,
    submissions: Sequence[Submission],
    options: dict[str, Any],
    *,
    log_fn: Any = None,
) -> None:
    """Create all rooms needed for *submissions* in a single bulk operation."""
    verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))

    room_names: set[str] = set()
    for submission in submissions:
        if submission.state not in (State.confirmed, State.accepted):
            continue
        data = SubmissionData(submission, event_slug, options.get("pretalx_base_url"))
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
        if log_fn:
            log_fn(
                f"Batch created {len(rooms_to_create)} rooms",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )
