"""
Talk CRUD and presentation-type mapping.

Delegates room lookups to :mod:`~.rooms` and individual speaker
operations to :mod:`~.speakers`.
"""

from typing import TYPE_CHECKING, Any

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
    "Open Space": "Open Space",
    "Panel": "Panel",
    "Plenary Session [Organizers]": "Plenary",
    "Plenary": "Plenary",
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
    room = (
        get_or_create_room(data.room, ctx, pretalx_id=data.pretalx_room_id) if data.room else None
    )

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


def _diff_talk_fields(
    talk: Talk,
    data: SubmissionData,
    ctx: ImportContext,
) -> dict[str, Any]:
    """
    Return the subset of fields whose new value differs from *talk*'s current value.

    Mirrors the assignment rules used by :func:`update_talk`: ``duration`` and
    ``external_image_url`` are only candidates when the source value is truthy, and
    ``event`` only when ``ctx.event_obj`` is set. Comparison uses ``!=`` (Django
    Model equality is PK-based, so two ``Room``/``Event`` instances pointing at the
    same row compare equal).
    """
    candidates: dict[str, Any] = {
        "title": data.title,
        "abstract": data.abstract,
        "description": data.description,
        "start_time": data.start_time,
        "room": get_or_create_room(data.room, ctx, pretalx_id=data.pretalx_room_id)
        if data.room
        else None,
        "track": data.track,
        "presentation_type": map_presentation_type(data.submission_type, data.code, ctx),
    }
    if data.duration:
        candidates["duration"] = data.duration
    if data.image_url:
        candidates["external_image_url"] = data.image_url
    if ctx.event_obj is not None:
        candidates["event"] = ctx.event_obj

    return {field: value for field, value in candidates.items() if getattr(talk, field) != value}


def update_talk(
    talk: Talk,
    data: SubmissionData,
    speakers: list[SubmissionSpeaker],
    ctx: ImportContext,
) -> bool:
    """
    Sync *talk* with fresh *data* and update its speakers.

    Only writes fields that actually differ (using ``save(update_fields=...)``),
    so calling this for a talk whose Pretalx data has not changed is a no-op.
    Speaker associations are synced via :func:`update_talk_speakers`.

    Returns ``True`` when at least one field or speaker association changed, ``False``
    when the talk and its speakers were already in sync. In ``--dry-run`` mode the diff
    is still computed (so the report is accurate) but no database writes occur.
    """
    changed_fields = _diff_talk_fields(talk, data, ctx)

    if changed_fields and not ctx.dry_run:
        for field, value in changed_fields.items():
            setattr(talk, field, value)
        # ``update_fields`` skips ``auto_now`` columns, so bump ``updated_at`` explicitly.
        talk.save(update_fields=[*changed_fields, "updated_at"])

    if changed_fields:
        ctx.log(
            f"Updated talk: {talk.title} (changed: {', '.join(sorted(changed_fields))})",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )

    speakers_changed = update_talk_speakers(talk, speakers, ctx)
    return bool(changed_fields) or speakers_changed


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
) -> bool:
    """
    Synchronize *talk*'s M2M speaker set with *submission_speakers*.

    Adds missing speakers and removes those no longer listed in the Pretalx
    submission. Respects ``--dry-run`` (computes the diff but skips writes) and
    ``--no-update`` (skips speaker syncing entirely).

    Returns ``True`` when at least one speaker was added or removed (or *would*
    be in dry-run mode), ``False`` otherwise.
    """
    if ctx.no_update:
        ctx.log(
            f"Skipping speaker updates due to --no-update flag: {talk.title}",
            VerbosityLevel.DETAILED,
            "WARNING",
        )
        return False

    current_ids = set(talk.speakers.all().values_list("pretalx_id", flat=True))
    new_ids = {sp.code for sp in submission_speakers}
    codes_to_add = new_ids - current_ids
    ids_to_remove = current_ids - new_ids

    if ctx.dry_run:
        if codes_to_add or ids_to_remove:
            ctx.log(
                f"Would update speakers for talk: {talk.title} (dry run)",
                VerbosityLevel.DETAILED,
            )
        return bool(codes_to_add) or bool(ids_to_remove)

    if codes_to_add:
        to_add = [
            get_or_create_speaker(sp, ctx) for sp in submission_speakers if sp.code in codes_to_add
        ]
        talk.speakers.add(*to_add)
        ctx.log(
            f"Added {len(to_add)} speakers to talk: {talk.title}",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )

    if ids_to_remove:
        objs = talk.speakers.filter(pretalx_id__in=ids_to_remove)
        removed = objs.count()
        talk.speakers.remove(*objs)
        ctx.log(
            f"Removed {removed} speakers from talk: {talk.title}",
            VerbosityLevel.DETAILED,
            "SUCCESS",
        )

    return bool(codes_to_add) or bool(ids_to_remove)
