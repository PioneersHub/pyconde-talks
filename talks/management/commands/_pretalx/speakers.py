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


#: Speaker columns synced from Pretalx on every import. ``pretalx_id`` is the
#: conflict key and stays out of the update set.
_SPEAKER_UPDATE_FIELDS: tuple[str, ...] = ("name", "biography", "avatar")


def batch_create_or_update_speakers(
    submissions: Sequence[Submission],
    ctx: ImportContext,
) -> set[str]:
    """
    Upsert all speakers from confirmed/accepted *submissions* in a single statement.

    Uses Django 4.1+ ``bulk_create(update_conflicts=True)`` so the database
    performs an ``INSERT ... ON CONFLICT DO UPDATE`` keyed on ``pretalx_id`` -
    one round-trip whether the speaker is new or already present. With
    ``--no-update`` the call falls back to ``ignore_conflicts=True`` so existing
    rows are left untouched.

    Returns the set of ``pretalx_id`` values whose visual fields (name or avatar)
    changed as part of this upsert. Callers use this set to decide which talks'
    social-card images need regenerating. With ``--no-update`` the returned set is
    empty (no existing rows are touched).
    """
    speakers_data = collect_speakers_from_submissions(submissions)
    if not speakers_data:
        return set()

    rows = [_build_speaker(data) for data in speakers_data.values()]

    if ctx.no_update:
        Speaker.objects.bulk_create(rows, ignore_conflicts=True)
        ctx.log(
            f"Batch upserted up to {len(rows)} speakers (--no-update: existing left as-is)",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
        return set()

    # Snapshot the existing visual fields BEFORE the upsert so we can spot avatar/name
    # changes. Done in one query keyed on the same pretalx_ids we're about to upsert.
    visual_changes = _detect_visual_changes(speakers_data)

    Speaker.objects.bulk_create(
        rows,
        update_conflicts=True,
        update_fields=list(_SPEAKER_UPDATE_FIELDS),
        unique_fields=["pretalx_id"],
    )
    ctx.log(
        f"Batch upserted {len(rows)} speakers",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )
    if visual_changes:
        ctx.log(
            f"{len(visual_changes)} speaker(s) had name/avatar changes "
            "(affected talks will be re-rendered)",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
    return visual_changes


def _detect_visual_changes(
    speakers_data: dict[str, SubmissionSpeaker],
) -> set[str]:
    """Return the set of ``pretalx_id`` values whose stored name/avatar differs from Pretalx."""
    existing = Speaker.objects.filter(pretalx_id__in=speakers_data.keys()).values_list(
        "pretalx_id",
        "name",
        "avatar",
    )
    changed: set[str] = set()
    for pretalx_id, current_name, current_avatar in existing:
        new = speakers_data[pretalx_id]
        if current_name != new.name or current_avatar != (new.avatar_url or ""):
            changed.add(pretalx_id)
    return changed


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _build_speaker(data: SubmissionSpeaker) -> Speaker:
    """Build a new (unsaved) Speaker from submission data."""
    return Speaker(
        name=data.name,
        biography=data.biography or "",
        avatar=data.avatar_url or "",
        pretalx_id=data.code,
    )
