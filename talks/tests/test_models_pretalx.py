"""Unit tests for :class:`talks.models.PendingPretalxChange`."""

from datetime import UTC, datetime

import pytest
from django.db.utils import IntegrityError
from model_bakery import baker

from events.models import Event
from talks.models import PendingPretalxChange, Talk


pytestmark = pytest.mark.django_db


def _make_event() -> Event:
    """Return a minimal saved Event for FK targets in these tests."""
    return Event.objects.create(slug="evt", name="Evt", year=2099)


class TestPendingChangeStatus:
    """``is_pending`` / ``is_applied`` / ``is_dismissed`` short-circuit on the timestamps."""

    def test_pending_when_no_timestamps_set(self) -> None:
        """A freshly detected row with no apply/dismiss timestamps is "pending"."""
        change = PendingPretalxChange(
            event=_make_event(),
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.UPDATE,
        )
        assert change.is_pending
        assert not change.is_applied
        assert not change.is_dismissed

    def test_mark_applied_sets_timestamp_and_user(self) -> None:
        """``mark_applied`` writes the timestamp+user and flips the status."""
        change = PendingPretalxChange.objects.create(
            event=_make_event(),
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.UPDATE,
        )
        change.mark_applied(user=None)
        change.refresh_from_db()
        assert change.is_applied
        assert change.applied_at is not None
        assert not change.is_pending

    def test_mark_dismissed_sets_timestamp(self) -> None:
        """``mark_dismissed`` writes the timestamp and flips the status."""
        change = PendingPretalxChange.objects.create(
            event=_make_event(),
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.UPDATE,
        )
        change.mark_dismissed(user=None)
        change.refresh_from_db()
        assert change.is_dismissed
        assert change.dismissed_at is not None
        assert not change.is_pending

    @pytest.mark.parametrize("action", ["mark_applied", "mark_dismissed"])
    def test_marking_preserves_last_detected_at(self, action: str) -> None:
        """Applying/dismissing must not rewrite last_detected_at (its audit + ordering value)."""
        change = PendingPretalxChange.objects.create(
            event=_make_event(),
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.UPDATE,
        )
        # last_detected_at is auto_now, so set a known past value via .update (which bypasses it).
        past = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
        PendingPretalxChange.objects.filter(pk=change.pk).update(last_detected_at=past)
        change.refresh_from_db()

        getattr(change, action)(user=None)
        change.refresh_from_db()

        assert change.last_detected_at == past


class TestUniqueOpenConstraint:
    """At most one open row per (event, pretalx_code)."""

    def test_second_open_row_is_rejected(self) -> None:
        """A second open row for the same submission violates the unique constraint."""
        event = _make_event()
        PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.UPDATE,
        )
        with pytest.raises(IntegrityError):
            PendingPretalxChange.objects.create(
                event=event,
                pretalx_code="ABC123",
                kind=PendingPretalxChange.Kind.UPDATE,
            )

    def test_second_open_allowed_after_first_applied(self) -> None:
        """Once the previous row is applied, the next detection can open a fresh row."""
        event = _make_event()
        first = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.UPDATE,
        )
        first.mark_applied(user=None)
        second = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.UPDATE,
        )
        assert second.pk != first.pk


class TestSummarize:
    """``summarize`` produces a single-line description for admin/email usage."""

    def test_create_summary_uses_payload_title(self) -> None:
        """CREATE summary should surface the incoming title from the payload."""
        change = PendingPretalxChange(
            event=_make_event(),
            pretalx_code="NEW001",
            kind=PendingPretalxChange.Kind.CREATE,
            pretalx_payload={"title": "Brand New Talk"},
        )
        assert "Brand New Talk" in change.summarize()
        assert "NEW001" in change.summarize()

    def test_update_summary_lists_field_names(self) -> None:
        """UPDATE summary lists field names plus speaker delta counts."""
        talk = baker.make(Talk, title="Original")
        change = PendingPretalxChange(
            event=_make_event(),
            pretalx_code="UPD001",
            talk=talk,
            kind=PendingPretalxChange.Kind.UPDATE,
            field_diffs={
                "title": {"old": "Original", "new": "Renamed"},
                "abstract": {"old": "x", "new": "y"},
            },
            speaker_diffs={"added": [{"code": "S1", "name": "A"}], "removed": []},
        )
        summary = change.summarize()
        assert "title" in summary
        assert "abstract" in summary
        assert "+1 speaker" in summary

    def test_delete_summary_uses_talk_title(self) -> None:
        """DELETE summary uses the local Talk title when present."""
        talk = baker.make(Talk, title="Doomed")
        change = PendingPretalxChange(
            event=_make_event(),
            pretalx_code="DEL001",
            talk=talk,
            kind=PendingPretalxChange.Kind.DELETE,
        )
        assert "Doomed" in change.summarize()
