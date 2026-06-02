"""Backfill Talk.event from each talk's room (else the newest event); see talks/talk_backfill.py."""

from typing import TYPE_CHECKING

from django.db import migrations

from talks.talk_backfill import backfill_talk_events


if TYPE_CHECKING:
    from django.db.backends.base.schema import BaseDatabaseSchemaEditor
    from django.db.migrations.state import StateApps


def forwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Assign Talk.event for every talk that still has none."""
    backfill_talk_events(
        talk_model=apps.get_model("talks", "Talk"),
        event_model=apps.get_model("events", "Event"),
        log=lambda msg: print(f"[0028_backfill_talk_event] {msg}"),
    )


def backwards(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """No-op: we can't tell which talks were event-less before the backfill."""


class Migration(migrations.Migration):

    dependencies = [
        ("talks", "0027_room_event_required"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
