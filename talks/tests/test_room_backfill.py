"""Tests for the Room.event backfill logic used by data migration 0025."""

import pytest
from model_bakery import baker

from events.models import Event
from talks.models import Room, Talk
from talks.room_backfill import backfill_room_events


def _run() -> None:
    backfill_room_events(room_model=Room, event_model=Event, talk_model=Talk)


@pytest.mark.django_db
class TestBackfillRoomEvents:
    """backfill_room_events assigns Room.event from talks, with safe fallbacks."""

    def test_assigns_event_from_talks(self) -> None:
        """A room whose talks all belong to one event gets that event."""
        event = Event.objects.create(slug="e", name="E", year=2026)
        room = Room.objects.create(name="Hall")
        baker.make(Talk, room=room, event=event)
        _run()
        room.refresh_from_db()
        assert room.event == event

    def test_room_with_no_talks_assigned_to_newest_event(self) -> None:
        """A room with no talks falls back to the newest event."""
        Event.objects.create(slug="old", name="Old", year=2024)
        newest = Event.objects.create(slug="new", name="New", year=2026)
        room = Room.objects.create(name="Empty")
        _run()
        room.refresh_from_db()
        assert room.event == newest

    def test_talks_without_event_use_fallback(self) -> None:
        """Talks that have no event don't pin the room; it falls back to newest."""
        newest = Event.objects.create(slug="e", name="E", year=2026)
        room = Room.objects.create(name="Hall")
        baker.make(Talk, room=room, event=None)
        _run()
        room.refresh_from_db()
        assert room.event == newest

    def test_cross_event_room_raises(self) -> None:
        """A room shared across events fails loud instead of guessing."""
        e1 = Event.objects.create(slug="e1", name="E1", year=2025)
        e2 = Event.objects.create(slug="e2", name="E2", year=2026)
        room = Room.objects.create(name="Shared")
        baker.make(Talk, room=room, event=e1)
        baker.make(Talk, room=room, event=e2)
        with pytest.raises(RuntimeError, match="multiple events"):
            _run()

    def test_idempotent_skips_already_assigned(self) -> None:
        """Rooms that already have an event are left untouched (re-run safe)."""
        event = Event.objects.create(slug="e", name="E", year=2026)
        other = Event.objects.create(slug="o", name="O", year=2027)
        room = Room.objects.create(name="Hall", event=event)
        baker.make(Talk, room=room, event=other)
        _run()
        room.refresh_from_db()
        assert room.event == event

    def test_no_events_leaves_room_unassigned(self) -> None:
        """With no events at all, a room with no talks is left unassigned (no crash)."""
        room = Room.objects.create(name="Hall")
        _run()
        room.refresh_from_db()
        assert room.event is None
