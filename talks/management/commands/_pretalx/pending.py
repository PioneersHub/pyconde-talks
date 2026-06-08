"""
Detect-only support for the Pretalx importer.

Helpers that turn the per-submission diff into ``PendingPretalxChange`` rows
without touching the live ``Talk``/``Speaker`` graph. Used by the importer when
``--detect-only`` is set so admins can review and apply changes from the admin
UI on their own schedule.
"""

import datetime
from typing import TYPE_CHECKING, Any

from django.db import IntegrityError, transaction

from talks.management.commands._pretalx.talks import (
    _diff_talk_fields,
    map_presentation_type,
)
from talks.models import PendingPretalxChange, Talk


if TYPE_CHECKING:
    from talks.management.commands._pretalx.context import ImportContext
    from talks.management.commands._pretalx.pretalx_models import SubmissionSpeaker
    from talks.management.commands._pretalx.submission import SubmissionData


# ------------------------------------------------------------------
# Diff helpers
# ------------------------------------------------------------------


def diff_speakers(
    talk: Talk | None,
    submission_speakers: list[SubmissionSpeaker],
) -> dict[str, list[dict[str, str]]]:
    """
    Return the symmetric difference between *talk*'s speakers and *submission_speakers*.

    Read-only: never touches the database. Output shape is
    ``{"added": [{"code", "name"}, ...], "removed": [{"code", "name"}, ...]}``,
    with empty lists when nothing changed. *talk* may be ``None`` for the CREATE
    case (every speaker counts as "added").
    """
    current: dict[str, str] = {}
    if talk is not None and talk.pk is not None:
        current = dict(talk.speakers.values_list("pretalx_id", "name"))
    new: dict[str, str] = {sp.code: sp.name for sp in submission_speakers}

    added = [{"code": code, "name": name} for code, name in new.items() if code not in current]
    removed = [{"code": code, "name": name} for code, name in current.items() if code not in new]
    return {"added": added, "removed": removed}


def serialize_field_diffs(
    talk: Talk,
    data: SubmissionData,
    ctx: ImportContext,
) -> dict[str, dict[str, Any]]:
    """
    Convert the dict returned by :func:`_diff_talk_fields` into JSON-friendly diffs.

    Output shape: ``{field_name: {"old": <value>, "new": <value>}}``. ``room`` and
    ``event`` are flattened to their string representation (slug or name) so the
    payload is safe to serialize. The ``room`` diff also carries ``old_pretalx_id`` /
    ``new_pretalx_id`` so apply can resolve and rename the room by its stable id.

    Read-only: ``_diff_talk_fields`` runs in detect mode, so room resolution never
    writes and the talk instance is never mutated.
    """
    fresh = _diff_talk_fields(talk, data, ctx)
    diffs: dict[str, dict[str, Any]] = {}
    for field, new_value in fresh.items():
        old_value = getattr(talk, field)
        entry: dict[str, Any] = {"old": _jsonify(old_value), "new": _jsonify(new_value)}
        if field == "room":
            entry["old_pretalx_id"] = getattr(old_value, "pretalx_id", None)
            entry["new_pretalx_id"] = data.pretalx_room_id
        diffs[field] = entry

    # A pure rename (Pretalx renamed the room the talk already sits in) leaves the talk's
    # room FK unchanged, so it never shows up in ``fresh``. Surface it explicitly so it is
    # reviewable in detect-only deployments instead of only applying on a direct sync.
    if "room" not in diffs:
        rename = _room_rename_diff(talk, data)
        if rename is not None:
            diffs["room"] = rename
    return diffs


def _room_rename_diff(talk: Talk, data: SubmissionData) -> dict[str, Any] | None:
    """
    Return a ``room`` diff when *talk*'s current room was renamed on Pretalx.

    A rename is detected when the talk's room carries the same stable Pretalx id as the
    incoming submission but a different name. Read-only; no DB access beyond the already
    loaded ``talk.room``.
    """
    current = talk.room
    if current is None or not data.room or data.pretalx_room_id is None:
        return None
    if current.pretalx_id == data.pretalx_room_id and current.name != data.room:
        return {
            "old": current.name,
            "new": data.room,
            "old_pretalx_id": current.pretalx_id,
            "new_pretalx_id": data.pretalx_room_id,
        }
    return None


def build_submission_payload(
    data: SubmissionData,
    submission_speakers: list[SubmissionSpeaker],
    ctx: ImportContext,
) -> dict[str, Any]:
    """
    Return the JSON-serializable snapshot of *data*, used as the apply payload.

    The snapshot is self-contained: applying the pending change later does not
    require re-fetching Pretalx. The shape mirrors what
    :func:`~_pretalx.talks.create_talk` / ``update_talk`` consume.
    """
    return {
        "title": data.title,
        "abstract": data.abstract,
        "description": data.description,
        "start_time": data.start_time.isoformat() if data.start_time else None,
        "duration_seconds": int(data.duration.total_seconds()) if data.duration else None,
        "room": data.room,
        "room_pretalx_id": data.pretalx_room_id,
        "track": data.track,
        "submission_type": data.submission_type,
        "presentation_type": map_presentation_type(data.submission_type, data.code, ctx),
        "image_url": data.image_url,
        "pretalx_link": data.pretalx_link,
        "speakers": [
            {
                "code": sp.code,
                "name": sp.name,
                "biography": getattr(sp, "biography", "") or "",
                "avatar_url": getattr(sp, "avatar_url", "") or "",
            }
            for sp in submission_speakers
        ],
    }


# ------------------------------------------------------------------
# Upsert helpers
# ------------------------------------------------------------------


def _find_open_change(event: Any, pretalx_code: str) -> PendingPretalxChange | None:
    """Return the single open (neither applied nor dismissed) row for the submission."""
    return PendingPretalxChange.objects.filter(
        event=event,
        pretalx_code=pretalx_code,
        applied_at__isnull=True,
        dismissed_at__isnull=True,
    ).first()


def _apply_updates(
    existing: PendingPretalxChange,
    values: dict[str, Any],
) -> PendingPretalxChange:
    """Refresh an existing open row in place with the latest diff/payload."""
    for field, value in values.items():
        setattr(existing, field, value)
    existing.save(update_fields=[*values, "last_detected_at"])
    return existing


def record_pending_change(  # noqa: PLR0913
    *,
    event: Any,
    pretalx_code: str,
    kind: str,
    talk: Talk | None,
    field_diffs: dict[str, dict[str, Any]],
    speaker_diffs: dict[str, list[dict[str, str]]],
    pretalx_payload: dict[str, Any],
) -> tuple[PendingPretalxChange, bool]:
    """
    Upsert an open ``PendingPretalxChange`` row for ``(event, pretalx_code)``.

    Returns ``(change, created)`` mirroring Django's ``get_or_create`` shape.
    When an open row already exists the existing record is updated in place;
    once it has been applied or dismissed a fresh row is created so the audit
    trail of past decisions is preserved.

    The create is wrapped in a savepoint and guarded against the partial unique
    constraint: if a concurrent detect run (e.g. cron racing the admin "Check
    Pretalx now" button) inserts the open row between our lookup and insert, the
    ``IntegrityError`` is caught, the savepoint keeps the surrounding request
    transaction usable, and we fall back to updating the row the other run created.
    """
    values: dict[str, Any] = {
        "kind": kind,
        "talk": talk,
        "field_diffs": field_diffs,
        "speaker_diffs": speaker_diffs,
        "pretalx_payload": pretalx_payload,
    }

    existing = _find_open_change(event, pretalx_code)
    if existing is not None:
        return _apply_updates(existing, values), False

    try:
        with transaction.atomic():
            change = PendingPretalxChange.objects.create(
                event=event,
                pretalx_code=pretalx_code,
                **values,
            )
    except IntegrityError:
        # Lost the race: another run created the open row first. Update it instead.
        existing = _find_open_change(event, pretalx_code)
        if existing is None:
            raise
        return _apply_updates(existing, values), False
    return change, True


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _jsonify(value: Any) -> Any:
    """
    Coerce a model-field value into something JSONField can store.

    Datetimes get ``.isoformat()``; timedeltas become integer seconds; FK
    instances render as their string repr (``Room.__str__`` is the room name,
    ``Event.__str__`` is the slug). Anything else passes through.
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return int(value.total_seconds())
    # Django model instances are not JSON-friendly; render to string.
    if hasattr(value, "_meta") and hasattr(value, "pk"):
        return str(value)
    return value
