"""
Talk CRUD and presentation-type mapping.

Delegates room lookups to :mod:`~.rooms` and individual speaker
operations to :mod:`~.speakers`.
"""

from typing import TYPE_CHECKING

from talks.management.commands._pretalx.rooms import get_or_create_room
from talks.management.commands._pretalx.speakers import get_or_create_speaker
from talks.management.commands._pretalx.types import VerbosityLevel
from talks.models import Talk


if TYPE_CHECKING:
    from pytanis.pretalx.models import SubmissionSpeaker

    from talks.management.commands._pretalx.context import ImportContext
    from talks.management.commands._pretalx.submission import SubmissionData


# ------------------------------------------------------------------
# Presentation-type mapping
# ------------------------------------------------------------------

#: Maps Pretalx submission-type labels to ``Talk.PresentationType`` values.
#: Unknown labels fall back to ``"Talk"`` at runtime via :func:`map_presentation_type`.
PRETALX_TYPE_MAPPING: dict[str, str] = {
    "Invited Talk": "Talk",
    "Keynote": "Keynote",
    "Kids Workshop": "Kids",
    "Lightning Talks": "Lightning",
    "Panel": "Panel",
    "Plenary Session [Organizers]": "Plenary",
    "Sponsored Talk (Keystone)": "Tutorial",
    "Sponsored Talk (long)": "Talk",
    "Sponsored Talk": "Talk",
    "Talk (long) [Sponsored]": "Talk",
    "Talk (long)": "Talk",
    "Talk [Sponsored]": "Talk",
    "Talk": "Talk",
    "Tutorial [Sponsored]": "Tutorial",
    "Tutorial": "Tutorial",
}


def map_presentation_type(
    submission_type: str | None,
    submission_code: str,
    ctx: ImportContext,
) -> str:
    """
    Map a Pretalx submission type to a ``Talk.PresentationType`` value.

    Falls back to :attr:`~talks.models.Talk.PresentationType.TALK` for
    unrecognized or empty types and logs a warning.
    """
    if not submission_type:
        ctx.log(
            f"Empty presentation type for submission {submission_code}, defaulting to 'Talk'",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
        return Talk.PresentationType.TALK

    mapped = PRETALX_TYPE_MAPPING.get(submission_type)
    if mapped is None:
        ctx.log(
            f"Unrecognized presentation type '{submission_type}' for "
            f"submission {submission_code}, defaulting to 'Talk'",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
        return Talk.PresentationType.TALK

    return mapped


# ------------------------------------------------------------------
# Talk CRUD
# ------------------------------------------------------------------


def create_talk(
    data: SubmissionData,
    ctx: ImportContext,
) -> Talk:
    """
    Persist a new :class:`~talks.models.Talk` from extracted *data*.

    Resolves the presentation type and room before inserting.
    """
    presentation_type = map_presentation_type(
        data.submission_type,
        data.code,
        ctx,
    )
    room = get_or_create_room(data.room, ctx) if data.room else None

    talk = Talk.objects.create(
        presentation_type=presentation_type,
        title=data.title,
        abstract=data.abstract,
        description=data.description,
        start_time=data.start_time,
        duration=data.duration,
        room=room,
        track=data.track,
        pretalx_link=data.pretalx_link,
        external_image_url=data.image_url,
        event=ctx.event_obj,
    )
    ctx.log(
        f"Created talk: {data.title}",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )
    return talk


def update_talk(
    talk: Talk,
    data: SubmissionData,
    speakers: list[SubmissionSpeaker],
    ctx: ImportContext,
) -> None:
    """
    Overwrite *talk* fields with fresh *data* and sync its speakers.

    Calls :func:`update_talk_speakers` after saving the talk itself.
    """
    talk.title = data.title
    talk.abstract = data.abstract
    talk.description = data.description
    talk.start_time = data.start_time

    if data.duration:
        talk.duration = data.duration
    talk.room = get_or_create_room(data.room, ctx) if data.room else None
    talk.track = data.track
    if data.image_url:
        talk.external_image_url = data.image_url
    talk.presentation_type = map_presentation_type(
        data.submission_type,
        data.code,
        ctx,
    )

    if ctx.event_obj and talk.event != ctx.event_obj:
        talk.event = ctx.event_obj
    talk.save()

    ctx.log(
        f"Updated talk: {talk.title}",
        VerbosityLevel.DETAILED,
        "SUCCESS",
    )
    update_talk_speakers(talk, speakers, ctx)


# ------------------------------------------------------------------
# Per-talk speaker management
# ------------------------------------------------------------------


def add_speakers_to_talk(
    talk: Talk,
    speakers: list[SubmissionSpeaker],
    ctx: ImportContext,
) -> None:
    """Attach *speakers* to a newly created *talk* (one-way add, no removal)."""
    for speaker_data in speakers:
        speaker = get_or_create_speaker(speaker_data, ctx)
        talk.speakers.add(speaker)
    if speakers:
        ctx.log(
            f"Added {len(speakers)} speakers to talk: {talk.title}",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )


def update_talk_speakers(
    talk: Talk,
    submission_speakers: list[SubmissionSpeaker],
    ctx: ImportContext,
) -> None:
    """
    Synchronize *talk*'s M2M speaker set with *submission_speakers*.

    Adds missing speakers and removes those no longer listed in the
    Pretalx submission.  Respects ``--dry-run`` and ``--no-update``.
    """
    if ctx.dry_run:
        ctx.log(
            f"Would update speakers for talk: {talk.title} (dry run)",
            VerbosityLevel.DETAILED,
        )
        return

    if ctx.no_update:
        ctx.log(
            f"Skipping speaker updates due to --no-update flag: {talk.title}",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
        return

    current_ids = set(talk.speakers.all().values_list("pretalx_id", flat=True))
    new_ids = {sp.code for sp in submission_speakers}

    to_add = [
        get_or_create_speaker(sp, ctx) for sp in submission_speakers if sp.code not in current_ids
    ]
    if to_add:
        talk.speakers.add(*to_add)
        ctx.log(
            f"Added {len(to_add)} speakers to talk: {talk.title}",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )

    ids_to_remove = current_ids - new_ids
    if ids_to_remove:
        objs = talk.speakers.filter(pretalx_id__in=ids_to_remove)
        removed = objs.count()
        talk.speakers.remove(*objs)
        ctx.log(
            f"Removed {removed} speakers from talk: {talk.title}",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )
