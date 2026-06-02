"""
Tests for the Talk.event backfill logic used by data migration 0028.

``Talk.event`` is NOT NULL on the current schema, so event-less talks can't be created
through the ORM anymore - but the backfill runs during migration 0028, while the column
is still nullable. We drive ``backfill_talk_events`` with small duck-typed fakes standing
in for the historical (nullable) models, plus a real-model no-op test against the live
schema. Migration 0028 applying during test-DB setup validates the query chain itself.
"""

from typing import Any

import pytest
from model_bakery import baker

from events.models import Event
from talks.models import Room, Talk
from talks.talk_backfill import backfill_talk_events


class _FakeEvent:
    def __init__(self, pk: int, slug: str) -> None:
        self.pk = pk
        self.slug = slug


class _FakeRoom:
    def __init__(self, event_id: int) -> None:
        self.event_id = event_id


class _FakeTalk:
    def __init__(self, pk: int, title: str, room: _FakeRoom | None = None) -> None:
        self.pk = pk
        self.title = title
        self.room = room
        self.room_id: int | None = None if room is None else 1
        self.event: _FakeEvent | None = None
        self.event_id: int | None = None
        self.saved_update_fields: list[str] | None = None

    def save(self, *, update_fields: list[str] | None = None) -> None:
        self.saved_update_fields = update_fields


class _FakeTalkModel:
    def __init__(self, talks: list[_FakeTalk]) -> None:
        self._talks = talks
        self.objects = self

    def filter(self, **_kwargs: Any) -> _FakeTalkModel:
        return self

    def select_related(self, *_args: Any) -> list[_FakeTalk]:
        return list(self._talks)


class _FakeEventModel:
    def __init__(self, newest: _FakeEvent | None) -> None:
        self._newest = newest
        self.objects = self

    def order_by(self, *_args: Any) -> _FakeEventModel:
        return self

    def first(self) -> _FakeEvent | None:
        return self._newest


def _run(talks: list[_FakeTalk], newest: _FakeEvent | None) -> None:
    backfill_talk_events(talk_model=_FakeTalkModel(talks), event_model=_FakeEventModel(newest))


class TestBackfillTalkEvents:
    """backfill_talk_events assigns Talk.event from its room, with a newest-event fallback."""

    def test_inherits_room_event(self) -> None:
        """A talk with a room inherits that room's (always coherent) event."""
        room_event_id = 5
        talk = _FakeTalk(1, "T", room=_FakeRoom(event_id=room_event_id))
        _run([talk], _FakeEvent(9, "newest"))
        assert talk.event_id == room_event_id
        assert talk.saved_update_fields == ["event"]

    def test_no_room_uses_newest_event(self) -> None:
        """A talk with no room falls back to the newest event."""
        talk = _FakeTalk(1, "T", room=None)
        newest = _FakeEvent(9, "newest")
        _run([talk], newest)
        assert talk.event is newest
        assert talk.saved_update_fields == ["event"]

    def test_no_room_no_event_left_unassigned(self) -> None:
        """With no events at all, a talk with no room is left untouched (no crash)."""
        talk = _FakeTalk(1, "T", room=None)
        _run([talk], None)
        assert talk.event is None
        assert talk.saved_update_fields is None

    @pytest.mark.django_db
    def test_real_models_noop_when_no_null_event_talks(self) -> None:
        """Against the live (NOT NULL) schema there are no null-event talks: safe no-op."""
        event = Event.objects.create(slug="e", name="E", year=2026)
        room = Room.objects.create(name="Hall", event=event)
        talk = baker.make(Talk, event=event, room=room)
        backfill_talk_events(talk_model=Talk, event_model=Event)
        talk.refresh_from_db()
        assert talk.event == event
