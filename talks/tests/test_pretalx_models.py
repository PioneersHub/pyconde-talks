"""
Unit tests for the Pretalx API response models.

These exercise the parse path and the navigation properties on :class:`Submission` directly,
independent of the HTTP client. (The Django ``PendingPretalxChange`` model is covered separately in
``test_models_pretalx.py``.)
"""
# ruff: noqa: PLR2004

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from talks.management.commands._pretalx.pretalx_models import (
    Event,
    MultiLingualStr,
    State,
    Submission,
)
from talks.tests._pretalx_factory import make_submission


class TestSubmissionNavigation:
    """The navigation properties dig the importer's values out of the nested shape."""

    def test_fully_populated_submission(self) -> None:
        """Every navigation property returns the expected nested value."""
        sub = make_submission(
            room="Hall A",
            room_id=7,
            track="PyData",
            submission_type="Talk",
            start=datetime(2026, 4, 14, 10, 0, tzinfo=UTC),
        )
        assert sub.first_slot is not None
        assert sub.start == datetime(2026, 4, 14, 10, 0, tzinfo=UTC)
        assert sub.room_name == "Hall A"
        assert sub.room_pretalx_id == 7
        assert sub.track_name == "PyData"
        assert sub.submission_type_name == "Talk"

    def test_unscheduled_submission_has_no_slot_or_room(self) -> None:
        """An unscheduled submission exposes None for slot/room properties."""
        sub = make_submission(room=None)

        assert sub.first_slot is None
        assert sub.start is None
        assert sub.room_name is None
        assert sub.room_pretalx_id is None

    def test_null_track_and_type(self) -> None:
        """Missing track / submission type yield None names rather than raising."""
        sub = make_submission(track=None, submission_type=None)

        assert sub.track_name is None
        assert sub.submission_type_name is None

    def test_room_present_but_id_null(self) -> None:
        """A scheduled room with no id yields room_pretalx_id None but keeps room_name."""
        sub = make_submission(room="Hall", room_id=None)

        assert sub.room_name == "Hall"
        assert sub.room_pretalx_id is None


class TestModelParsing:
    """Validation behaviour of the trimmed models."""

    def test_extra_keys_are_ignored(self) -> None:
        """Unmodelled keys are dropped (``extra="ignore"``) and do not appear on the instance."""
        sub = Submission.model_validate(
            {
                "code": "X1",
                "title": "Talk",
                "state": "confirmed",
                "speakers": [],
                "internal_notes": "secret",  # unmodelled -> dropped
                "track": {"id": 1, "name": {"en": "PyData"}, "color": "#fff"},  # color dropped
            },
        )

        assert sub.code == "X1"
        assert sub.track is not None
        assert sub.track.name.en == "PyData"
        assert not hasattr(sub, "internal_notes")

    def test_state_is_parsed_to_enum(self) -> None:
        """The string ``state`` is coerced to the :class:`State` enum."""
        assert make_submission(state="withdrawn").state is State.withdrawn

    def test_unknown_state_raises(self) -> None:
        """An unrecognized state value is a validation error."""
        with pytest.raises(ValidationError):
            Submission.model_validate(
                {"code": "X", "title": "T", "state": "not-a-state", "speakers": []},
            )

    def test_missing_required_field_raises(self) -> None:
        """A submission without a title fails validation."""
        with pytest.raises(ValidationError):
            Submission.model_validate({"code": "X", "state": "confirmed"})

    def test_multilingual_extra_languages_preserved(self) -> None:
        """Languages beyond en/de are retained on a multilingual string."""
        name = MultiLingualStr.model_validate({"en": "Hi", "de": "Hallo", "fr": "kept"})

        assert name.en == "Hi"
        assert name.model_dump().get("fr") == "kept"


class TestEvent:
    """The Event model only needs the name and slug."""

    def test_parses_name_and_slug(self) -> None:
        """Event validation keeps name/slug and ignores everything else."""
        event = Event.model_validate({"name": {"en": "PyCon"}, "slug": "pyc", "is_public": True})

        assert event.name.en == "PyCon"
        assert event.slug == "pyc"
