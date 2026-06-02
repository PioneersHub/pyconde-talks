"""Unit tests for :func:`apply_change` (apply a PendingPretalxChange to the live DB)."""

from datetime import UTC, datetime, timedelta

import pytest
from model_bakery import baker

from events.models import Event
from talks.management.commands._pretalx.apply import apply_change
from talks.models import PendingPretalxChange, Room, Speaker, Talk


pytestmark = pytest.mark.django_db


def _make_event() -> Event:
    """Return a minimal saved Event for FK targets in these tests."""
    return Event.objects.create(slug="evt", name="Evt", year=2099)


class TestApplyCreate:
    """CREATE rows materialize a full Talk with speakers + room."""

    def test_creates_talk_room_and_speakers(self) -> None:
        """Apply CREATE produces a new Talk, attaches speakers, and reuses/creates a Room."""
        event = _make_event()
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="NEW001",
            kind=PendingPretalxChange.Kind.CREATE,
            pretalx_payload={
                "title": "Brand New Talk",
                "abstract": "abs",
                "description": "desc",
                "start_time": datetime(2026, 4, 29, 10, 0, tzinfo=UTC).isoformat(),
                "duration_seconds": 1800,
                "room": "Main Hall",
                "track": "PyData",
                "submission_type": "Talk",
                "presentation_type": "Talk",
                "image_url": "",
                "pretalx_link": "https://pretalx/talk/NEW001",
                "speakers": [
                    {
                        "code": "S1",
                        "name": "Ada Lovelace",
                        "biography": "",
                        "avatar_url": "",
                    },
                ],
            },
        )

        talk = apply_change(change)

        assert talk is not None
        assert talk.title == "Brand New Talk"
        assert talk.room is not None
        assert talk.room.name == "Main Hall"
        assert talk.duration == timedelta(minutes=30)
        assert {s.pretalx_id for s in talk.speakers.all()} == {"S1"}
        assert Room.objects.filter(name="Main Hall").exists()

        change.refresh_from_db()
        assert change.is_applied


class TestApplyUpdate:
    """UPDATE rows only touch fields listed in ``field_diffs``; other fields survive."""

    def test_updates_only_diffed_fields(self) -> None:
        """Local edits to fields *not* in the diff are not overwritten."""
        event = _make_event()
        talk = baker.make(
            Talk,
            title="Old Title",
            abstract="Old abstract",
            description="Manual description (not from Pretalx)",
            event=event,
        )
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="UPD001",
            talk=talk,
            kind=PendingPretalxChange.Kind.UPDATE,
            field_diffs={
                "title": {"old": "Old Title", "new": "New Title"},
                "abstract": {"old": "Old abstract", "new": "New abstract"},
            },
            speaker_diffs={"added": [], "removed": []},
            pretalx_payload={"speakers": []},
        )

        apply_change(change)

        talk.refresh_from_db()
        assert talk.title == "New Title"
        assert talk.abstract == "New abstract"
        # Description was a manual local edit; not in the diff, so it survives.
        assert talk.description == "Manual description (not from Pretalx)"

    def test_room_diff_resolves_existing_room(self) -> None:
        """A room-name diff looks up or creates the matching Room."""
        event = _make_event()
        new_room = Room.objects.create(name="Auditorium")
        talk = baker.make(Talk, event=event, room=None)
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="UPD002",
            talk=talk,
            kind=PendingPretalxChange.Kind.UPDATE,
            field_diffs={"room": {"old": None, "new": "Auditorium"}},
            speaker_diffs={"added": [], "removed": []},
            pretalx_payload={"speakers": []},
        )

        apply_change(change)

        talk.refresh_from_db()
        assert talk.room == new_room

    def test_speaker_added_via_pending_change(self) -> None:
        """Added speakers come from the payload snapshot when not already in the DB."""
        event = _make_event()
        talk = baker.make(Talk, event=event)
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="UPD003",
            talk=talk,
            kind=PendingPretalxChange.Kind.UPDATE,
            field_diffs={},
            speaker_diffs={
                "added": [{"code": "NEW", "name": "Newcomer"}],
                "removed": [],
            },
            pretalx_payload={
                "speakers": [
                    {"code": "NEW", "name": "Newcomer", "biography": "Bio", "avatar_url": ""},
                ],
            },
        )

        apply_change(change)

        assert {s.pretalx_id for s in talk.speakers.all()} == {"NEW"}
        assert Speaker.objects.get(pretalx_id="NEW").biography == "Bio"

    def test_speaker_removed_via_pending_change(self) -> None:
        """Removed speakers get detached but the Speaker row itself is left alone."""
        event = _make_event()
        talk = baker.make(Talk, event=event)
        stale = Speaker.objects.create(name="Stale", pretalx_id="OLD")
        talk.speakers.add(stale)
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="UPD004",
            talk=talk,
            kind=PendingPretalxChange.Kind.UPDATE,
            field_diffs={},
            speaker_diffs={"added": [], "removed": [{"code": "OLD", "name": "Stale"}]},
            pretalx_payload={"speakers": []},
        )

        apply_change(change)

        assert not talk.speakers.filter(pretalx_id="OLD").exists()
        # Speaker row still exists (might be on other talks); only the link was removed.
        assert Speaker.objects.filter(pretalx_id="OLD").exists()


class TestApplyDelete:
    """DELETE rows remove the target Talk."""

    def test_deletes_target_talk(self) -> None:
        """Apply DELETE removes the Talk row referenced by the pending change."""
        event = _make_event()
        talk = baker.make(Talk, title="Doomed", event=event)
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="DEL001",
            talk=talk,
            kind=PendingPretalxChange.Kind.DELETE,
        )

        result = apply_change(change)

        assert result is None
        assert not Talk.objects.filter(pk=talk.pk).exists()
        change.refresh_from_db()
        assert change.is_applied

    def test_delete_with_missing_talk_is_noop(self) -> None:
        """DELETE for an already-removed Talk still marks the change applied."""
        event = _make_event()
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="DEL002",
            kind=PendingPretalxChange.Kind.DELETE,
            talk=None,
        )

        apply_change(change)

        change.refresh_from_db()
        assert change.is_applied


class TestApplyGuards:
    """``apply_change`` refuses to act on a row that is already closed."""

    def test_applying_already_applied_raises(self) -> None:
        """A second apply on the same row is refused (avoids double-mutation)."""
        event = _make_event()
        change = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="X",
            kind=PendingPretalxChange.Kind.DELETE,
        )
        apply_change(change)
        with pytest.raises(ValueError, match="already applied"):
            apply_change(change)
