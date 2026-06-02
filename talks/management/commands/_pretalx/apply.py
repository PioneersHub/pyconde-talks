"""
Apply a previously detected :class:`~talks.models.PendingPretalxChange`.

Each pending row carries enough information to (re)create the change without
re-fetching Pretalx: ``field_diffs`` holds the per-field "new" values for an
UPDATE, ``pretalx_payload`` holds the full snapshot for a CREATE. Applying the
row mutates the local DB and flips ``applied_at`` so the same change cannot be
applied twice.
"""

import datetime
from typing import TYPE_CHECKING, Any

from django.db import transaction

from talks.models import (
    EMPTY_TRACK_NAME,
    FAR_FUTURE,
    PendingPretalxChange,
    Room,
    Speaker,
    Talk,
)


if TYPE_CHECKING:
    from users.models import CustomUser


#: Fields on ``Talk`` that the apply step is allowed to overwrite. ``room`` and
#: ``event`` are handled separately because they are foreign keys, not strings.
_DIRECT_TALK_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "abstract",
        "description",
        "track",
        "presentation_type",
        "external_image_url",
    },
)


def apply_change(
    change: PendingPretalxChange,
    *,
    user: CustomUser | None = None,
) -> Talk | None:
    """
    Apply *change* to the local database and mark it applied.

    Returns the affected :class:`~talks.models.Talk` (or ``None`` for DELETE).
    Wrapped in a single transaction so a partial failure leaves nothing applied
    and the pending row untouched.
    """
    if not change.is_pending:
        msg = f"Pending change {change.pk} is already applied or dismissed."
        raise ValueError(msg)

    with transaction.atomic():
        match change.kind:
            case PendingPretalxChange.Kind.CREATE:
                talk = _apply_create(change)
            case PendingPretalxChange.Kind.UPDATE:
                talk = _apply_update(change)
            case PendingPretalxChange.Kind.DELETE:
                _apply_delete(change)
                talk = None
            case _:
                msg = f"Unknown pending change kind: {change.kind!r}"
                raise ValueError(msg)
        change.mark_applied(user=user)
    return talk


# ------------------------------------------------------------------
# Per-kind apply helpers
# ------------------------------------------------------------------


def _apply_create(change: PendingPretalxChange) -> Talk:
    """Materialize a new ``Talk`` (and its speakers + room) from *change.pretalx_payload*."""
    payload: dict[str, Any] = change.pretalx_payload or {}
    room = _resolve_room(payload.get("room", ""))
    talk = Talk.objects.create(
        presentation_type=payload.get("presentation_type", Talk.PresentationType.TALK),
        title=payload.get("title", ""),
        abstract=payload.get("abstract", ""),
        description=payload.get("description", ""),
        start_time=_parse_datetime(payload.get("start_time")),
        duration=_parse_duration(payload.get("duration_seconds")),
        room=room,
        track=payload.get("track") or EMPTY_TRACK_NAME,
        external_image_url=payload.get("image_url") or "",
        pretalx_link=payload.get("pretalx_link", ""),
        event=change.event,
    )
    _attach_speakers(talk, payload.get("speakers", []))
    return talk


def _apply_update(change: PendingPretalxChange) -> Talk:
    """
    Apply ``field_diffs`` and ``speaker_diffs`` onto *change.talk*.

    Only fields recorded in ``field_diffs`` are touched - any manual local edits
    to *other* fields are preserved.
    """
    talk: Talk | None = change.talk
    if talk is None:
        msg = (
            f"PendingPretalxChange {change.pk} has no target talk; the row was "
            "likely deleted after detection. Re-run --detect-only to refresh."
        )
        raise ValueError(msg)

    updated_fields = _apply_field_diffs(talk, change.field_diffs)
    if updated_fields:
        talk.save(update_fields=[*updated_fields, "updated_at"])

    _sync_speakers(talk, change.speaker_diffs, change.pretalx_payload or {})
    return talk


def _apply_delete(change: PendingPretalxChange) -> None:
    """Delete the talk referenced by *change* (no-op if it has already been removed)."""
    if change.talk is None:
        return
    change.talk.delete()
    # The FK is ``on_delete=SET_NULL`` so the DB row now points at NULL, but the
    # in-memory ``change`` still references the (now unsaved) ``Talk`` object.
    # Clear it explicitly so the subsequent ``mark_applied`` save does not trip
    # Django's "unsaved related object" guard.
    change.talk = None


# ------------------------------------------------------------------
# Field / speaker plumbing
# ------------------------------------------------------------------


def _apply_field_diffs(
    talk: Talk,
    field_diffs: dict[str, dict[str, Any]],
) -> list[str]:
    """Write each diff's ``new`` value onto *talk* and return the list of touched fields."""
    updated: list[str] = []
    for field, change in field_diffs.items():
        new_value = change.get("new")
        if field in _DIRECT_TALK_FIELDS:
            setattr(talk, field, new_value if new_value is not None else "")
            updated.append(field)
        elif field == "start_time":
            talk.start_time = _parse_datetime(new_value)
            updated.append(field)
        elif field == "duration":
            talk.duration = _parse_duration(new_value)
            updated.append(field)
        elif field == "room":
            talk.room = _resolve_room(new_value or "")
            updated.append(field)
        # ``event`` diffs are deliberately ignored: the pending row already has a
        # FK to the event, and re-pointing a Talk at a different Event mid-conference
        # is risky. Re-create the talk under the new event instead.
    return updated


def _sync_speakers(
    talk: Talk,
    speaker_diffs: dict[str, list[dict[str, Any]]],
    payload: dict[str, Any],
) -> None:
    """Add new speakers (creating them on the fly if needed) and remove dropped ones."""
    added = speaker_diffs.get("added", [])
    removed = speaker_diffs.get("removed", [])
    if not added and not removed:
        return

    speakers_lookup = {sp["code"]: sp for sp in payload.get("speakers", []) if "code" in sp}

    for sp in added:
        code = sp["code"]
        snapshot = speakers_lookup.get(code, sp)
        speaker, _ = Speaker.objects.get_or_create(
            pretalx_id=code,
            defaults={
                "name": snapshot.get("name", ""),
                "biography": snapshot.get("biography") or "",
                "avatar": snapshot.get("avatar_url") or "",
            },
        )
        talk.speakers.add(speaker)

    if removed:
        codes_to_remove = [sp["code"] for sp in removed if "code" in sp]
        to_drop = talk.speakers.filter(pretalx_id__in=codes_to_remove)
        talk.speakers.remove(*to_drop)


def _attach_speakers(talk: Talk, speakers: list[dict[str, Any]]) -> None:
    """Resolve or create each speaker in *speakers* and attach to *talk*."""
    for sp in speakers:
        code = sp.get("code")
        if not code:
            continue
        speaker, _ = Speaker.objects.get_or_create(
            pretalx_id=code,
            defaults={
                "name": sp.get("name", ""),
                "biography": sp.get("biography") or "",
                "avatar": sp.get("avatar_url") or "",
            },
        )
        talk.speakers.add(speaker)


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------


def _resolve_room(room_name: str) -> Room | None:
    """Get-or-create a :class:`~talks.models.Room` by name; empty input returns ``None``."""
    if not room_name:
        return None
    room, _ = Room.objects.get_or_create(
        name=room_name,
        defaults={"description": f"Room imported from Pretalx: {room_name}"},
    )
    return room


def _parse_datetime(value: Any) -> datetime.datetime:
    """Parse an ISO datetime string back to ``datetime``; fall back to ``FAR_FUTURE``."""
    if not value:
        return FAR_FUTURE
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(value)


def _parse_duration(value: Any) -> datetime.timedelta:
    """Parse stored integer seconds back to a ``timedelta``; ``None`` becomes zero."""
    if value is None:
        return datetime.timedelta()
    if isinstance(value, datetime.timedelta):
        return value
    return datetime.timedelta(seconds=int(value))
