"""Backfill Room.event from each room's talks (see talks/room_backfill.py)."""

from typing import TYPE_CHECKING

from django.db import migrations

from talks.room_backfill import backfill_room_events


if TYPE_CHECKING:
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
    from django.db.migrations.state import StateApps


def forwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Assign Room.event for every room that still has none."""
    backfill_room_events(
        room_model=apps.get_model("talks", "Room"),
        event_model=apps.get_model("events", "Event"),
        talk_model=apps.get_model("talks", "Talk"),
        log=lambda msg: print(f"[0025_backfill_room_event] {msg}"),
    )


def backwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """
    Best-effort reverse: clear the event on every room.

    We cannot reconstruct which rooms were NULL before, so this nulls them all.
    pretalx_id is left untouched (it is not set by this migration).
    """
    apps.get_model("talks", "Room").objects.update(event=None)


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0024_room_event_pretalx_id"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
