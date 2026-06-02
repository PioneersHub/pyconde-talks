"""
Tests for the Room.event backfill logic used by data migration 0025.

``Room.event`` is NOT NULL on the current schema, so event-less rooms can't be created
through the ORM anymore - but the backfill runs during migration 0025, while the column
is still nullable. To exercise every branch we drive ``backfill_room_events`` with small
duck-typed fakes that stand in for the historical (nullable) Room/Event/Talk models. A
final real-model test confirms it stays a safe no-op against the live schema (where no
null-event rooms exist), and migration 0025 applying during test-DB setup validates the
query chain itself.
"""

from typing import Any

import pytest
from model_bakery import baker

from events.models import Event
from talks.models import Room, Talk
from talks.room_backfill import backfill_room_events


class _FakeEvent:
    def __init__(self, pk: int, slug: str) -> None:
        self.pk = pk
        self.slug = slug


class _FakeRoom:
    def __init__(self, pk: int, name: str) -> None:
        self.pk = pk
        self.name = name
        self.event: _FakeEvent | None = None
        self.event_id: int | None = None
        self.saved_update_fields: list[str] | None = None

    def save(self, *, update_fields: list[str] | None = None) -> None:
        self.saved_update_fields = update_fields


class _FakeQuery:
    def __init__(self, ids: list[int]) -> None:
        self._ids = ids

    def values_list(self, *_args: Any, flat: bool = False) -> _FakeQuery:
        return self

    def distinct(self) -> list[int]:
        return list(self._ids)


class _FakeTalkModel:
    def __init__(self, ids_by_room: dict[int, list[int]]) -> None:
        self._ids_by_room = ids_by_room
        self.objects = self

    def filter(self, *, room: _FakeRoom, **_kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self._ids_by_room.get(room.pk, []))


class _FakeRoomModel:
    def __init__(self, rooms: list[_FakeRoom]) -> None:
        self._rooms = rooms
        self.objects = self

    def filter(self, **_kwargs: Any) -> list[_FakeRoom]:
        return list(self._rooms)


class _FakeEventModel:
    def __init__(self, newest: _FakeEvent | None) -> None:
        self._newest = newest
        self.objects = self

    def order_by(self, *_args: Any) -> _FakeEventModel:
        return self

    def first(self) -> _FakeEvent | None:
        return self._newest


def _run(
    rooms: list[_FakeRoom],
    ids_by_room: dict[int, list[int]],
    newest: _FakeEvent | None,
) -> None:
    backfill_room_events(
        room_model=_FakeRoomModel(rooms),
        event_model=_FakeEventModel(newest),
        talk_model=_FakeTalkModel(ids_by_room),
    )


class TestBackfillRoomEvents:
    """backfill_room_events assigns Room.event from talks, with safe fallbacks."""

    def test_assigns_event_from_single_talk_event(self) -> None:
        """A room whose talks all belong to one event gets that event."""
        talk_event_id = 7
        room = _FakeRoom(1, "Hall")
        _run([room], {1: [talk_event_id]}, _FakeEvent(9, "newest"))
        assert room.event_id == talk_event_id
        assert room.saved_update_fields == ["event"]

    def test_room_with_no_talks_uses_newest_event(self) -> None:
        """A room with no talks falls back to the newest event."""
        room = _FakeRoom(1, "Empty")
        newest = _FakeEvent(9, "newest")
        _run([room], {1: []}, newest)
        assert room.event is newest
        assert room.saved_update_fields == ["event"]

    def test_room_with_no_talks_and_no_event_left_unassigned(self) -> None:
        """With no events at all, a room with no talks is left untouched (no crash)."""
        room = _FakeRoom(1, "Empty")
        _run([room], {1: []}, None)
        assert room.event is None
        assert room.saved_update_fields is None

    def test_cross_event_room_raises(self) -> None:
        """A room shared across events fails loud instead of guessing."""
        room = _FakeRoom(1, "Shared")
        with pytest.raises(RuntimeError, match="multiple events"):
            _run([room], {1: [1, 2]}, _FakeEvent(9, "newest"))

    def test_no_rooms_is_noop(self) -> None:
        """No null-event rooms means nothing to do and no error."""
        _run([], {}, _FakeEvent(9, "newest"))

    @pytest.mark.django_db
    def test_real_models_noop_when_no_null_event_rooms(self) -> None:
        """Against the live (NOT NULL) schema there are no null-event rooms: safe no-op."""
        event = Event.objects.create(slug="e", name="E", year=2026)
        room = Room.objects.create(name="Hall", event=event)
        baker.make(Talk, room=room, event=event)
        backfill_room_events(room_model=Room, event_model=Event, talk_model=Talk)
        room.refresh_from_db()
        assert room.event == event
