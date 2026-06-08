"""
Builders for real Pretalx API model instances in tests.

Tests used to hand-mock submissions with ``unittest.mock.Mock``, which silently accepts any
attribute and so could not catch a model-shape regression. These helpers instead build the same
API-shaped JSON the live endpoint returns and run it through ``Submission.model_validate``, so a
test exercises the genuine parse path. Keep the keyword arguments close to the fields a test cares
about; everything else gets a sensible default.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from talks.management.commands._pretalx.pretalx_models import (
    State,
    Submission,
    SubmissionSpeaker,
)


if TYPE_CHECKING:
    from collections.abc import Sequence

#: Default slot start used when a test does not care about scheduling.
DEFAULT_START = datetime(2099, 6, 1, 10, 0, tzinfo=UTC)


def make_speaker(
    code: str = "SPK-1",
    name: str = "Ada Lovelace",
    biography: str = "",
    avatar_url: str = "",
) -> SubmissionSpeaker:
    """Build a validated :class:`SubmissionSpeaker` from primitive fields."""
    return SubmissionSpeaker.model_validate(
        {"code": code, "name": name, "biography": biography, "avatar_url": avatar_url},
    )


def make_submission(  # noqa: PLR0913  # a test builder with one knob per field is fine
    *,
    code: str = "SUB-1",
    title: str = "A Talk",
    abstract: str = "abs",
    description: str = "desc",
    state: State | str = State.confirmed,
    submission_type: str | None = "Talk",
    track: str | None = "PyData",
    room: str | None = "Main Hall",
    room_id: int | None = 4993,
    duration: int | None = 30,
    start: datetime | str | None = DEFAULT_START,
    image: str | None = None,
    speakers: Sequence[SubmissionSpeaker] | None = None,
) -> Submission:
    """
    Build a validated :class:`Submission` shaped like the live API response.

    ``submission_type``/``track``/``room`` take the English name (or ``None`` to omit the object).
    ``room=None`` (or empty) produces a submission with no scheduled slot.
    """
    state_value = state.value if isinstance(state, State) else state
    speaker_models = [make_speaker()] if speakers is None else list(speakers)

    slots: list[dict[str, object]] = []
    if room:
        start_value = start.isoformat() if isinstance(start, datetime) else start
        slots = [{"start": start_value, "room": {"id": room_id, "name": _name(room)}}]

    raw = {
        "code": code,
        "title": title,
        "abstract": abstract,
        "description": description,
        "state": state_value,
        "duration": duration,
        "image": image,
        "slot_count": len(slots),
        "speakers": [sp.model_dump() for sp in speaker_models],
        "submission_type": {"id": 1, "name": _name(submission_type)} if submission_type else None,
        "track": {"id": 2, "name": _name(track)} if track else None,
        "slots": slots,
    }
    return Submission.model_validate(raw)


def _name(value: str | None) -> dict[str, str | None]:
    """Wrap *value* as the API's multilingual-name object (``{"en": ...}``)."""
    return {"en": value}
