"""Batch speaker create/update operations to reduce database round-trips."""

from typing import TYPE_CHECKING, Any

from pytanis.pretalx.models import State

from talks.management.commands._pretalx.types import VerbosityLevel
from talks.models import Speaker


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytanis.pretalx.models import Submission, SubmissionSpeaker


def collect_speakers_from_submissions(
    submissions: Sequence[Submission],
) -> dict[str, SubmissionSpeaker]:
    """Collect unique speakers from accepted/confirmed submissions."""
    speakers_data: dict[str, SubmissionSpeaker] = {}
    valid_states = {State.confirmed, State.accepted}
    for submission in submissions:
        if submission.state not in valid_states or not submission.speakers:
            continue
        for speaker in submission.speakers:
            speakers_data[speaker.code] = speaker
    return speakers_data


def batch_create_or_update_speakers(
    submissions: Sequence[Submission],
    options: dict[str, Any],
    *,
    log_fn: Any = None,
) -> None:
    """
    Batch create or update all speakers to reduce database queries.

    Parameters
    ----------
    submissions:
        Full list of Pretalx submissions (any state).
    options:
        Command options dict (``verbosity``, ``no_update``, …).
    log_fn:
        Optional callable ``(message, verbosity, min_level, style)`` for logging.

    """
    verbosity = VerbosityLevel(options.get("verbosity", VerbosityLevel.NORMAL.value))
    no_update = options.get("no_update", False)

    speakers_data = collect_speakers_from_submissions(submissions)
    if not speakers_data:
        return

    existing_speakers = {
        s.pretalx_id: s for s in Speaker.objects.filter(pretalx_id__in=speakers_data.keys())
    }

    speakers_to_create: list[Speaker] = []
    speakers_to_update: list[Speaker] = []

    for code, speaker_data in speakers_data.items():
        if code not in existing_speakers:
            speakers_to_create.append(
                Speaker(
                    name=speaker_data.name,
                    biography=speaker_data.biography or "",
                    avatar=speaker_data.avatar_url or "",
                    pretalx_id=speaker_data.code,
                ),
            )
        elif not no_update:
            existing = existing_speakers[code]
            bio = speaker_data.biography or ""
            avatar = speaker_data.avatar_url or ""
            if (
                existing.name != speaker_data.name
                or existing.biography != bio
                or existing.avatar != avatar
            ):
                existing.name = speaker_data.name
                existing.biography = bio
                existing.avatar = avatar
                speakers_to_update.append(existing)

    if speakers_to_create:
        Speaker.objects.bulk_create(speakers_to_create, ignore_conflicts=True)
        if log_fn:
            log_fn(
                f"Batch created {len(speakers_to_create)} speakers",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )

    if speakers_to_update:
        Speaker.objects.bulk_update(speakers_to_update, ["name", "biography", "avatar"])
        if log_fn:
            log_fn(
                f"Batch updated {len(speakers_to_update)} speakers",
                verbosity,
                VerbosityLevel.DETAILED,
                "SUCCESS",
            )
