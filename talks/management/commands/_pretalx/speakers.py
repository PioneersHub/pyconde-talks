"""Speaker create/update helpers - single lookup and batch bulk operations."""

from typing import TYPE_CHECKING

from pytanis.pretalx.models import State

from talks.management.commands._pretalx.types import VerbosityLevel
from talks.models import Speaker


if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytanis.pretalx.models import Submission, SubmissionSpeaker

    from talks.management.commands._pretalx.context import ImportContext


# ------------------------------------------------------------------
# Single speaker helpers
# ------------------------------------------------------------------


def get_or_create_speaker(
    speaker_data: SubmissionSpeaker,
    ctx: ImportContext,
) -> Speaker:
    """
    Return the :class:`~talks.models.Speaker` for *speaker_data*, creating it if needed.

    If the speaker already exists it is updated via :func:`maybe_update_speaker`
    (when flags allow).  In ``--dry-run`` mode an unsaved instance is returned.
    """
    existing = Speaker.objects.filter(pretalx_id=speaker_data.code).first()
    if existing:
        maybe_update_speaker(existing, speaker_data, ctx)
        return existing

    if ctx.dry_run:
        ctx.log(
            f"Would create speaker: {speaker_data.name} (dry run)",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        return Speaker(
            name=speaker_data.name,
            biography=speaker_data.biography or "",
            avatar=speaker_data.avatar_url or "",
            pretalx_id=speaker_data.code,
        )

    speaker = Speaker.objects.create(
        name=speaker_data.name,
        biography=speaker_data.biography or "",
        avatar=speaker_data.avatar_url or "",
        pretalx_id=speaker_data.code,
    )
    ctx.log(
        f"Created speaker: {speaker_data.name}",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )
    return speaker


def maybe_update_speaker(
    existing: Speaker,
    speaker_data: SubmissionSpeaker,
    ctx: ImportContext,
) -> None:
    """
    Update *existing* speaker when data differs and flags permit.

    No-ops when ``--no-update`` or ``--dry-run`` is active, or when the
    fields already match.
    """
    if ctx.no_update:
        ctx.log(
            f"Skipping update for existing speaker: {speaker_data.name} (--no-update)",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
        return

    if ctx.dry_run:
        return

    bio = speaker_data.biography or ""
    avatar = speaker_data.avatar_url or ""
    if (
        existing.name == speaker_data.name
        and existing.biography == bio
        and existing.avatar == avatar
    ):
        return

    existing.name = speaker_data.name
    existing.biography = bio
    existing.avatar = avatar
    existing.save()
    ctx.log(
        f"Updated speaker: {speaker_data.name}",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )


def collect_speakers_from_submissions(
    submissions: Sequence[Submission],
) -> dict[str, SubmissionSpeaker]:
    """
    Collect unique speakers from confirmed/accepted *submissions*.

    Returns a ``{pretalx_code: SubmissionSpeaker}`` mapping, deduplicating
    speakers that appear in multiple submissions.
    """
    speakers_data: dict[str, SubmissionSpeaker] = {}
    valid_states = {State.confirmed, State.accepted}
    for submission in submissions:
        if submission.state not in valid_states or not submission.speakers:
            continue
        for speaker in submission.speakers:
            speakers_data[speaker.code] = speaker
    return speakers_data


# ------------------------------------------------------------------
# Public entry-point
# ------------------------------------------------------------------


def batch_create_or_update_speakers(
    submissions: Sequence[Submission],
    ctx: ImportContext,
) -> None:
    """
    Bulk-create new speakers and bulk-update changed ones.

    Processes all confirmed/accepted *submissions* in two DB round-trips
    (one ``bulk_create`` + one ``bulk_update``).
    """
    speakers_data = collect_speakers_from_submissions(submissions)
    if not speakers_data:
        return

    existing = {
        s.pretalx_id: s for s in Speaker.objects.filter(pretalx_id__in=speakers_data.keys())
    }

    to_create, to_update = _partition_speakers(
        speakers_data,
        existing,
        no_update=ctx.no_update,
    )
    _bulk_create_speakers(to_create, ctx)
    _bulk_update_speakers(to_update, ctx)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _partition_speakers(
    speakers_data: dict[str, SubmissionSpeaker],
    existing: dict[str, Speaker],
    *,
    no_update: bool,
) -> tuple[list[Speaker], list[Speaker]]:
    """Split speakers into *to_create* and *to_update* lists."""
    to_create: list[Speaker] = []
    to_update: list[Speaker] = []
    for code, data in speakers_data.items():
        if code not in existing:
            to_create.append(_build_speaker(data))
        elif not no_update and _speaker_changed(existing[code], data):
            _apply_speaker_data(existing[code], data)
            to_update.append(existing[code])
    return to_create, to_update


def _build_speaker(data: SubmissionSpeaker) -> Speaker:
    """Build a new (unsaved) Speaker from submission data."""
    return Speaker(
        name=data.name,
        biography=data.biography or "",
        avatar=data.avatar_url or "",
        pretalx_id=data.code,
    )


def _speaker_changed(existing: Speaker, data: SubmissionSpeaker) -> bool:
    """Return ``True`` if the existing speaker differs from submission data."""
    return (
        existing.name != data.name
        or existing.biography != (data.biography or "")
        or existing.avatar != (data.avatar_url or "")
    )


def _apply_speaker_data(speaker: Speaker, data: SubmissionSpeaker) -> None:
    """Copy submission data onto an existing speaker (without saving)."""
    speaker.name = data.name
    speaker.biography = data.biography or ""
    speaker.avatar = data.avatar_url or ""


def _bulk_create_speakers(
    speakers: list[Speaker],
    ctx: ImportContext,
) -> None:
    """Bulk-create speakers and log the result."""
    if not speakers:
        return
    Speaker.objects.bulk_create(speakers, ignore_conflicts=True)
    ctx.log(
        f"Batch created {len(speakers)} speakers",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )


def _bulk_update_speakers(
    speakers: list[Speaker],
    ctx: ImportContext,
) -> None:
    """Bulk-update speakers and log the result."""
    if not speakers:
        return
    Speaker.objects.bulk_update(speakers, ["name", "biography", "avatar"])
    ctx.log(
        f"Batch updated {len(speakers)} speakers",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )
