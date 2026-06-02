"""
Unit tests for the import_pretalx_talks management command.

Tests cover the core logic without making actual API calls.
"""
# ruff: noqa: PLR2004
# mypy: disable-error-code="arg-type"

from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import Mock, patch

import pytest
from django.db import IntegrityError
from model_bakery import baker
from pytanis.pretalx.models import State

from events.models import Event
from talks.management.commands._pretalx import pending as pending_mod
from talks.management.commands._pretalx.context import ImportContext
from talks.management.commands._pretalx.pending import record_pending_change
from talks.management.commands._pretalx.rooms import batch_create_rooms
from talks.management.commands._pretalx.speakers import (
    batch_create_or_update_speakers,
    collect_speakers_from_submissions,
)
from talks.management.commands._pretalx.submission import (
    SubmissionData,
    submission_is_announcement,
    submission_is_lightning_talk,
)
from talks.management.commands._pretalx.talks import map_presentation_type
from talks.management.commands._pretalx.types import LogFn, VerbosityLevel
from talks.management.commands._pretalx.validation import is_valid_submission
from talks.management.commands.import_pretalx_talks import Command
from talks.models import (
    FAR_FUTURE,
    MAX_TALK_TITLE_LENGTH,
    PendingPretalxChange,
    Room,
    Speaker,
    Talk,
)


def _noop_log(
    message: str,
    verbosity: VerbosityLevel,
    min_level: VerbosityLevel,
    style: str | None = None,
) -> None:
    """Silent log function for tests that don't need logging output."""


_noop: LogFn = _noop_log


def _ctx(log_fn: LogFn = _noop, **overrides: Any) -> ImportContext:
    """Build an :class:`ImportContext` with sensible test defaults."""
    return ImportContext(verbosity=VerbosityLevel.NORMAL, log_fn=log_fn, **overrides)


# ---------------------- Fixtures ----------------------


@pytest.fixture()
def command() -> Command:
    """Create a Command instance with mocked stdout/stderr."""
    cmd = Command()
    cmd.stdout = StringIO()  # type: ignore[assignment]
    cmd.stderr = StringIO()  # type: ignore[assignment]
    return cmd


@pytest.fixture()
def mock_submission() -> Mock:
    """Create a mock Submission object with minimal valid data."""
    submission = Mock()
    submission.code = "ABC123"
    submission.title = "Test Talk Title"
    submission.abstract = "Test abstract"
    submission.description = "Test description"
    submission.state = State.confirmed
    submission.duration = 45

    # Mock slots with room and start time
    slot = Mock()
    slot.room = Mock()
    slot.room.name = {"en": "Main Hall"}
    slot.start = datetime(2024, 6, 15, 10, 0, tzinfo=UTC)
    submission.slots = [slot]

    # Mock track
    submission.track = Mock()
    submission.track.name = Mock()
    submission.track.name.en = "Data Science"

    # Mock submission_type
    submission.submission_type = Mock()
    submission.submission_type.en = "Talk"

    # Mock speakers
    speaker1 = Mock()
    speaker1.code = "SPK001"
    speaker1.name = "John Cleese"
    speaker1.biography = "Speaker bio"
    speaker1.avatar_url = "https://example.com/avatar.jpg"

    speaker2 = Mock()
    speaker2.code = "SPK002"
    speaker2.name = "Eric Idle"
    speaker2.biography = "Another bio"
    speaker2.avatar_url = "https://example.com/avatar2.jpg"

    submission.speakers = [speaker1, speaker2]
    submission.image = ""

    return submission


@pytest.fixture()
def mock_submission_no_speakers(mock_submission: Mock) -> Mock:
    """Create a mock Submission without speakers."""
    mock_submission.speakers = []
    return mock_submission


@pytest.fixture()
def mock_submission_lightning(mock_submission: Mock) -> Mock:
    """Create a mock Lightning Talk submission."""
    mock_submission.submission_type.en = "Lightning Talks"
    mock_submission.speakers = []
    return mock_submission


# ---------------------- SubmissionData Tests ----------------------


class TestSubmissionData:
    """Tests for the SubmissionData class."""

    def test_basic_data_extraction(self, mock_submission: Mock) -> None:
        """Test that SubmissionData extracts basic fields correctly."""
        data = SubmissionData(
            mock_submission,
            pretalx_event_url="https://pretalx.com/pyconde2024",
        )

        assert data.code == "ABC123"
        assert data.title == "Test Talk Title"
        assert data.abstract == "Test abstract"
        assert data.description == "Test description"
        assert data.room == "Main Hall"
        assert data.track == "Data Science"
        assert data.submission_type == "Talk"
        assert data.pretalx_link == "https://pretalx.com/pyconde2024/talk/ABC123"

    def test_custom_base_url(self, mock_submission: Mock) -> None:
        """Test that custom base URL is properly used."""
        data = SubmissionData(
            mock_submission,
            pretalx_event_url="https://custom.pretalx.com/pyconde2099",
        )

        assert data.pretalx_link == "https://custom.pretalx.com/pyconde2099/talk/ABC123"

    def test_trailing_slash_handling(self, mock_submission: Mock) -> None:
        """Test that trailing slash is handled correctly."""
        data = SubmissionData(
            mock_submission,
            pretalx_event_url="https://pretalx.com/pyconde2099//",
        )

        assert data.pretalx_link == "https://pretalx.com/pyconde2099/talk/ABC123"

    def test_missing_room(self, mock_submission: Mock) -> None:
        """Test handling of submission without room."""
        mock_submission.slots = []
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.room == ""

    def test_missing_track(self, mock_submission: Mock) -> None:
        """Test handling of submission without track."""
        mock_submission.track = None
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.track == ""

    def test_title_truncation(self, mock_submission: Mock) -> None:
        """Test that long titles are truncated."""
        mock_submission.title = "A" * (MAX_TALK_TITLE_LENGTH + 50)
        data = SubmissionData(mock_submission, "pyconde2099")

        assert len(data.title) == MAX_TALK_TITLE_LENGTH

    def test_duration_extraction(self, mock_submission: Mock) -> None:
        """Test that duration is extracted as timedelta."""
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.duration == timedelta(minutes=45)

    def test_missing_duration(self, mock_submission: Mock) -> None:
        """Test handling of submission without duration."""
        mock_submission.duration = None
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.duration is None

    def test_start_time_extraction(self, mock_submission: Mock) -> None:
        """Test that start time is extracted from slots."""
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.start_time == datetime(2024, 6, 15, 10, 0, tzinfo=UTC)

    def test_missing_start_time(self, mock_submission: Mock) -> None:
        """Test handling of submission without start time."""
        mock_submission.slots[0].start = None
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.start_time == FAR_FUTURE

    def test_empty_title_handling(self, mock_submission: Mock) -> None:
        """Test handling of empty title."""
        mock_submission.title = None
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.title == ""

    def test_pretalx_room_id_from_room_id_field(self, mock_submission: Mock) -> None:
        """The flat slot.room_id is used as the stable room id when present."""
        mock_submission.slots[0].room_id = 4993
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.pretalx_room_id == 4993

    def test_pretalx_room_id_falls_back_to_nested(self, mock_submission: Mock) -> None:
        """When slot.room_id is absent, fall back to the nested slot.room.id."""
        mock_submission.slots[0].room_id = None
        mock_submission.slots[0].room.id = 4993
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.pretalx_room_id == 4993

    def test_pretalx_room_id_none_when_no_slot(self, mock_submission: Mock) -> None:
        """No slots means no room id."""
        mock_submission.slots = []
        data = SubmissionData(mock_submission, "pyconde2099")

        assert data.pretalx_room_id is None


# ---------------------- _is_valid_submission Tests ----------------------


@pytest.mark.django_db
class TestIsValidSubmission:
    """Tests for the _is_valid_submission method."""

    def test_valid_submission(self, command: Command, mock_submission: Mock) -> None:
        """Test that valid submission passes validation."""
        ctx = _ctx(log_fn=command._log)
        result = is_valid_submission(mock_submission, ctx)

        assert result is True

    def test_missing_title(self, command: Command, mock_submission: Mock) -> None:
        """Test that submission without title fails validation."""
        mock_submission.title = None
        ctx = _ctx(log_fn=command._log)
        result = is_valid_submission(mock_submission, ctx)

        assert result is False

    @patch("talks.management.commands._pretalx.validation.settings")
    def test_missing_speakers_regular_talk(
        self,
        mock_settings: Mock,
        command: Command,
        mock_submission_no_speakers: Mock,
    ) -> None:
        """Test that submission without speakers follows IMPORT_TALKS_WITHOUT_SPEAKERS setting."""
        mock_settings.IMPORT_TALKS_WITHOUT_SPEAKERS = False
        mock_submission_no_speakers.submission_type.en = "Talk"
        ctx = _ctx(log_fn=command._log)

        result = is_valid_submission(
            mock_submission_no_speakers,
            ctx,
        )

        assert result is False

    def test_lightning_talk_without_speakers(
        self,
        command: Command,
        mock_submission_lightning: Mock,
    ) -> None:
        """Test that Lightning Talks are allowed without speakers."""
        ctx = _ctx(log_fn=command._log)
        result = is_valid_submission(
            mock_submission_lightning,
            ctx,
        )

        assert result is True


# ---------------------- _map_presentation_type Tests ----------------------


@pytest.mark.django_db
class TestMapPresentationType:
    """Tests for the _map_presentation_type method."""

    @pytest.mark.parametrize(
        ("input_type", "expected"),
        [
            ("Talk", Talk.PresentationType.TALK),
            ("Talk (long)", Talk.PresentationType.TALK),
            ("Tutorial", Talk.PresentationType.TUTORIAL),
            ("Keynote", Talk.PresentationType.KEYNOTE),
            ("Lightning Talks", Talk.PresentationType.LIGHTNING),
            ("Panel", Talk.PresentationType.PANEL),
            ("Kids Workshop", Talk.PresentationType.KIDS),
            ("Plenary Session [Organizers]", Talk.PresentationType.PLENARY),
        ],
    )
    def test_known_type_mapping(
        self,
        input_type: str,
        expected: str,
    ) -> None:
        """Test mapping of known presentation types."""
        result = map_presentation_type(
            input_type,
            "ABC123",
            _ctx(),
        )

        assert result == expected

    def test_unknown_type_defaults_to_talk(self) -> None:
        """Test that unknown types default to Talk."""
        result = map_presentation_type(
            "Unknown Type",
            "ABC123",
            _ctx(),
        )

        assert result == Talk.PresentationType.TALK

    def test_empty_type_defaults_to_talk(self) -> None:
        """Test that empty/None types default to Talk."""
        ctx = _ctx()
        result = map_presentation_type("", "ABC123", ctx)
        assert result == Talk.PresentationType.TALK

        result = map_presentation_type(None, "ABC123", ctx)
        assert result == Talk.PresentationType.TALK


# ---------------------- batch_create_rooms Tests ----------------------


@pytest.mark.django_db
class TestBatchCreateRooms:
    """Tests for the batch_create_rooms function."""

    def test_creates_new_rooms(self, mock_submission: Mock) -> None:
        """Test that new rooms are created via bulk_create."""
        mock_submission.state = State.confirmed
        submissions = [mock_submission]
        ctx = _ctx()

        batch_create_rooms(submissions, ctx)

        assert Room.objects.filter(name="Main Hall").exists()

    def test_skips_existing_rooms(self, mock_submission: Mock) -> None:
        """Test that existing rooms are not recreated."""
        # Create existing room
        Room.objects.create(name="Main Hall", description="Original description")

        mock_submission.state = State.confirmed
        submissions = [mock_submission]
        ctx = _ctx(pretalx_event_url="https://pretalx.com/pyconde2099")

        batch_create_rooms(submissions, ctx)

        # Should still be only one room
        assert Room.objects.filter(name="Main Hall").count() == 1
        # Description should not change
        assert Room.objects.get(name="Main Hall").description == "Original description"

    def test_skips_non_confirmed_submissions(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test that submissions not in confirmed/accepted state are skipped."""
        mock_submission.state = State.submitted
        submissions = [mock_submission]
        ctx = _ctx()

        batch_create_rooms(submissions, ctx)

        assert not Room.objects.filter(name="Main Hall").exists()

    def test_handles_multiple_unique_rooms(self, mock_submission: Mock) -> None:
        """Test creating multiple unique rooms from submissions."""
        # First submission
        mock_submission.state = State.confirmed

        # Second submission with different room
        submission2 = Mock()
        submission2.code = "DEF456"
        submission2.title = "Another Talk"
        submission2.abstract = ""
        submission2.description = ""
        submission2.state = State.confirmed
        submission2.duration = 30
        submission2.image = ""
        slot2 = Mock()
        slot2.room = Mock()
        slot2.room.name = {"en": "Workshop Room"}
        slot2.start = datetime(2024, 6, 15, 14, 0, tzinfo=UTC)
        submission2.slots = [slot2]
        submission2.track = None  # No track
        submission2.submission_type = Mock()
        submission2.submission_type.en = "Talk"
        submission2.speakers = mock_submission.speakers

        submissions = [mock_submission, submission2]
        ctx = _ctx()

        batch_create_rooms(submissions, ctx)

        assert Room.objects.count() == 2
        assert Room.objects.filter(name="Main Hall").exists()
        assert Room.objects.filter(name="Workshop Room").exists()


# ---------------------- batch_create_or_update_speakers Tests ----------------------


@pytest.mark.django_db
class TestBatchCreateOrUpdateSpeakers:
    """Tests for the batch_create_or_update_speakers function."""

    def test_creates_new_speakers(self, mock_submission: Mock) -> None:
        """Test that new speakers are bulk created."""
        mock_submission.state = State.confirmed
        submissions = [mock_submission]
        ctx = _ctx()

        batch_create_or_update_speakers(submissions, ctx)

        assert Speaker.objects.filter(pretalx_id="SPK001").exists()
        assert Speaker.objects.filter(pretalx_id="SPK002").exists()
        assert Speaker.objects.count() == 2

    def test_updates_existing_speakers(self, mock_submission: Mock) -> None:
        """Test that existing speakers are bulk updated."""
        # Create existing speaker with old data
        Speaker.objects.create(
            name="Old Name",
            biography="Old bio",
            avatar="",
            pretalx_id="SPK001",
        )

        mock_submission.state = State.confirmed
        submissions = [mock_submission]
        ctx = _ctx(no_update=False)

        batch_create_or_update_speakers(submissions, ctx)

        speaker = Speaker.objects.get(pretalx_id="SPK001")
        assert speaker.name == "John Cleese"
        assert speaker.biography == "Speaker bio"

    def test_skips_update_with_no_update_flag(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test that existing speakers are not updated when no_update is True."""
        # Create existing speaker
        Speaker.objects.create(
            name="Old Name",
            biography="Old bio",
            avatar="",
            pretalx_id="SPK001",
        )

        mock_submission.state = State.confirmed
        submissions = [mock_submission]
        ctx = _ctx(no_update=True)

        batch_create_or_update_speakers(submissions, ctx)

        speaker = Speaker.objects.get(pretalx_id="SPK001")
        assert speaker.name == "Old Name"  # Should not be updated

    def test_deduplicates_speakers_across_submissions(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test that the same speaker appearing in multiple submissions is only created once."""
        mock_submission.state = State.confirmed

        # Second submission with same speaker
        submission2 = Mock()
        submission2.code = "DEF456"
        submission2.state = State.confirmed
        submission2.speakers = [mock_submission.speakers[0]]  # Same speaker
        slot = Mock()
        slot.room = Mock()
        slot.room.name = {"en": "Room B"}
        slot.start = datetime(2024, 6, 15, 14, 0, tzinfo=UTC)
        submission2.slots = [slot]

        submissions = [mock_submission, submission2]
        ctx = _ctx()

        batch_create_or_update_speakers(submissions, ctx)

        # SPK001 should only be created once
        assert Speaker.objects.filter(pretalx_id="SPK001").count() == 1

    def test_returns_changed_visual_ids_for_avatar_change(
        self,
        mock_submission: Mock,
    ) -> None:
        """Speakers whose avatar URL changed are reported back so talk images can re-render."""
        Speaker.objects.create(
            name="John Cleese",
            biography="Speaker bio",
            avatar="https://example.com/old-avatar.jpg",
            pretalx_id="SPK001",
        )
        # SPK002 is brand new - new speakers are not "visually changed".
        mock_submission.state = State.confirmed
        mock_submission.speakers[0].avatar_url = "https://example.com/NEW-avatar.jpg"

        changed = batch_create_or_update_speakers([mock_submission], _ctx())

        assert changed == {"SPK001"}

    def test_returns_empty_set_with_no_update(self, mock_submission: Mock) -> None:
        """With --no-update existing rows are untouched, so nothing visual changed."""
        Speaker.objects.create(
            name="Old",
            biography="",
            avatar="https://example.com/old.jpg",
            pretalx_id="SPK001",
        )
        mock_submission.state = State.confirmed
        mock_submission.speakers[0].avatar_url = "https://example.com/new.jpg"

        changed = batch_create_or_update_speakers([mock_submission], _ctx(no_update=True))

        assert changed == set()


# ---------------------- collect_speakers_from_submissions Tests ----------------------


class TestCollectSpeakersFromSubmissions:
    """Tests for the collect_speakers_from_submissions function."""

    def test_collects_speakers_from_valid_submissions(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test that speakers are collected from confirmed/accepted submissions."""
        mock_submission.state = State.confirmed
        submissions = [mock_submission]

        result = collect_speakers_from_submissions(submissions)

        assert len(result) == 2
        assert "SPK001" in result
        assert "SPK002" in result

    def test_skips_invalid_states(self, mock_submission: Mock) -> None:
        """Test that speakers from non-confirmed submissions are skipped."""
        mock_submission.state = State.submitted
        submissions = [mock_submission]

        result = collect_speakers_from_submissions(submissions)

        assert len(result) == 0

    def test_deduplicates_speakers(self, mock_submission: Mock) -> None:
        """Test that duplicate speakers are deduplicated."""
        mock_submission.state = State.confirmed

        # Create second submission with overlapping speakers
        submission2 = Mock()
        submission2.code = "DEF456"
        submission2.state = State.confirmed
        submission2.speakers = [mock_submission.speakers[0]]  # Same speaker

        submissions = [mock_submission, submission2]

        result = collect_speakers_from_submissions(submissions)

        assert len(result) == 2  # Only 2 unique speakers


# ---------------------- Helper Method Tests ----------------------


class TestHelperMethods:
    """Tests for helper functions."""

    def test_submission_is_lightning_talk_with_type(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test lightning talk detection via submission_type."""
        mock_submission.submission_type.en = "Lightning Talks"

        result = submission_is_lightning_talk(mock_submission)

        assert result is True

    def test_submission_is_lightning_talk_with_track(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test lightning talk detection via track."""
        mock_submission.track.en = "Lightning"

        result = submission_is_lightning_talk(mock_submission)

        assert result is True

    def test_submission_is_not_lightning_talk(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test that regular talks are not detected as lightning talks."""
        result = submission_is_lightning_talk(mock_submission)

        assert result is False

    def test_submission_is_announcement(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test announcement detection."""
        mock_submission.title = "Opening Session"

        result = submission_is_announcement(mock_submission)

        assert result is True

    def test_submission_is_not_announcement(
        self,
        mock_submission: Mock,
    ) -> None:
        """Test that regular talks are not detected as announcements."""
        result = submission_is_announcement(mock_submission)

        assert result is False


# ---------------------- VerbosityLevel Tests ----------------------


class TestVerbosityLevel:
    """Tests for VerbosityLevel enum."""

    def test_verbosity_values(self) -> None:
        """Test that verbosity levels have expected values."""
        assert VerbosityLevel.MINIMAL.value == 0
        assert VerbosityLevel.NORMAL.value == 1
        assert VerbosityLevel.DETAILED.value == 2
        assert VerbosityLevel.DEBUG.value == 3
        assert VerbosityLevel.TRACE.value == 4


# ---------------------- _log Tests ----------------------


class TestLogMethod:
    """Tests for the _log method."""

    def test_log_at_normal_verbosity(self, command: Command) -> None:
        """Test that messages at normal verbosity are logged."""
        command._log("Test message", VerbosityLevel.NORMAL, VerbosityLevel.NORMAL)

        assert "Test message" in command.stdout.getvalue()

    def test_log_skipped_when_below_threshold(self, command: Command) -> None:
        """Test that messages below threshold are not logged."""
        command._log("Test message", VerbosityLevel.NORMAL, VerbosityLevel.DETAILED)

        assert "Test message" not in command.stdout.getvalue()

    def test_log_with_error_style(self, command: Command) -> None:
        """Test that error style logs to stderr."""
        command._log("Error message", VerbosityLevel.NORMAL, VerbosityLevel.NORMAL, style="ERROR")

        assert "Error message" in command.stderr.getvalue()


# ---------------------- Integration-like Tests (with DB) ----------------------


@pytest.mark.django_db
class TestProcessSingleSubmission:
    """Tests for _process_single_submission method."""

    @patch("talks.management.commands._pretalx.mixins.create_talk")
    @patch("talks.management.commands._pretalx.mixins.add_speakers_to_talk")
    def test_creates_new_talk(
        self,
        mock_add_speakers: Mock,
        mock_create_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """Test that new talks are created."""
        mock_submission.state = State.confirmed

        # Create required room and speakers
        room = Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        # Mock create_talk to return a Talk
        talk = baker.make(Talk, title="Test Talk", room=room)
        mock_create_talk.return_value = talk

        ctx = _ctx(
            log_fn=command._log,
            skip_images=True,
            pretalx_event_url="https://pretalx.com/pyconde2099",
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "created"
        mock_create_talk.assert_called_once()

    def test_dry_run_does_not_create(
        self,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """Test that dry_run mode does not actually create talks but returns 'created'."""
        mock_submission.state = State.confirmed
        Room.objects.create(name="Main Hall")

        ctx = _ctx(
            log_fn=command._log,
            dry_run=True,
            skip_images=True,
            pretalx_event_url="https://pretalx.com",
        )

        result = command._process_single_submission(mock_submission, ctx)

        # Returns "created" to indicate what would happen, but no DB changes
        assert result == "created"
        assert Talk.objects.count() == 0

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_updates_existing_talk_and_regenerates_image(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """Test that existing talks are updated and images are regenerated."""
        mock_submission.state = State.confirmed

        room = Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        existing_talk = baker.make(
            Talk,
            title="Old Title",
            room=room,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )

        ctx = _ctx(
            log_fn=command._log,
            skip_images=True,
            pretalx_event_url=pretalx_url,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "updated"
        mock_update_talk.assert_called_once_with(
            existing_talk,
            mock_update_talk.call_args[0][1],  # SubmissionData
            mock_submission.speakers,
            ctx,
        )

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_update_generates_image_when_not_skipped(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """Test that image generation runs on update when skip_images is False."""
        mock_submission.state = State.confirmed

        room = Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        existing_talk = baker.make(
            Talk,
            title="Old Title",
            room=room,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )

        command._image_generator = Mock()

        ctx = _ctx(
            log_fn=command._log,
            skip_images=False,
            pretalx_event_url=pretalx_url,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "updated"
        command._image_generator.generate.assert_called_once_with(existing_talk, ctx)

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_update_skips_image_when_skip_images_set(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """Test that image generation is skipped on update when skip_images is True."""
        mock_submission.state = State.confirmed

        room = Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        baker.make(
            Talk,
            title="Old Title",
            room=room,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )

        command._image_generator = Mock()

        ctx = _ctx(
            log_fn=command._log,
            skip_images=True,
            pretalx_event_url=pretalx_url,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "updated"
        command._image_generator.generate.assert_not_called()

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_unchanged_talk_does_not_regenerate_image(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """When update_talk reports no changes, the result is 'unchanged' and no image is built."""
        mock_update_talk.return_value = False
        mock_submission.state = State.confirmed

        room = Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        baker.make(
            Talk,
            title="Old Title",
            room=room,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )

        command._image_generator = Mock()

        ctx = _ctx(
            log_fn=command._log,
            skip_images=False,
            pretalx_event_url=pretalx_url,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "unchanged"
        command._image_generator.generate.assert_not_called()

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_regenerates_when_attached_speaker_visually_changed(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """An avatar/name change on a still-attached speaker triggers image regen."""
        mock_update_talk.return_value = False
        mock_submission.state = State.confirmed
        Room.objects.create(name="Main Hall")
        speaker = Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        existing_talk = baker.make(
            Talk,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )
        existing_talk.speakers.add(speaker)
        command._image_generator = Mock()
        # Simulate what _process_submissions populates.
        command._speakers_with_visual_change = frozenset({"SPK001"})

        ctx = _ctx(log_fn=command._log, skip_images=False, pretalx_event_url=pretalx_url)

        command._process_single_submission(mock_submission, ctx)

        command._image_generator.generate.assert_called_once_with(existing_talk, ctx)

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_force_images_regenerates_unchanged_talk(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """--force-images regenerates the image even when nothing changed."""
        mock_update_talk.return_value = False
        mock_submission.state = State.confirmed

        room = Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        existing_talk = baker.make(
            Talk,
            title="Old Title",
            room=room,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )
        command._image_generator = Mock()

        ctx = _ctx(
            log_fn=command._log,
            skip_images=False,
            force_images=True,
            pretalx_event_url=pretalx_url,
        )

        result = command._process_single_submission(mock_submission, ctx)

        # Status still reflects the data diff: nothing changed.
        assert result == "unchanged"
        command._image_generator.generate.assert_called_once_with(existing_talk, ctx)

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_skip_images_overrides_force_images(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """--skip-images wins over --force-images when both are set."""
        mock_update_talk.return_value = False
        mock_submission.state = State.confirmed
        Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        baker.make(
            Talk,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )
        command._image_generator = Mock()

        ctx = _ctx(
            log_fn=command._log,
            skip_images=True,
            force_images=True,
            pretalx_event_url=pretalx_url,
        )

        command._process_single_submission(mock_submission, ctx)

        command._image_generator.generate.assert_not_called()

    @patch("talks.management.commands._pretalx.mixins.update_talk")
    def test_regenerates_when_template_newer_than_image(
        self,
        mock_update_talk: Mock,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """A template touched after the existing image triggers image regen."""
        mock_update_talk.return_value = False
        mock_submission.state = State.confirmed
        Room.objects.create(name="Main Hall")
        Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")

        pretalx_url = "https://pretalx.com/pyconde2099"
        baker.make(
            Talk,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
        )
        command._image_generator = Mock()
        # Talk has no saved image (falsy) -> image_is_older_than returns True for any threshold.
        # Pretend the template was touched at epoch 1.0.
        command._template_mtime = 1.0

        ctx = _ctx(log_fn=command._log, skip_images=False, pretalx_event_url=pretalx_url)

        command._process_single_submission(mock_submission, ctx)

        command._image_generator.generate.assert_called_once()


# ---------------------- detect-only Tests ----------------------


@pytest.mark.django_db
class TestDetectOnlyMode:
    """``--detect-only`` records changes as PendingPretalxChange rows without touching Talks."""

    def test_detect_create_writes_pending_row(
        self,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """A new submission produces a CREATE pending row and leaves the Talk table alone."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        mock_submission.state = State.confirmed
        pretalx_url = "https://pretalx.com/evt"

        ctx = _ctx(
            log_fn=command._log,
            detect_only=True,
            skip_images=True,
            pretalx_event_url=pretalx_url,
            event_obj=event,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "detected"
        assert Talk.objects.count() == 0
        change = PendingPretalxChange.objects.get()
        assert change.kind == PendingPretalxChange.Kind.CREATE
        assert change.pretalx_code == mock_submission.code
        assert change.talk is None
        # Speakers should land in the diff so reviewers can see them.
        assert {s["code"] for s in change.speaker_diffs["added"]} == {"SPK001", "SPK002"}

    def test_detect_update_writes_pending_row_without_touching_talk(
        self,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """A diff against an existing Talk produces an UPDATE pending row, no DB writes to Talk."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        mock_submission.state = State.confirmed
        pretalx_url = "https://pretalx.com/evt"
        existing_talk = baker.make(
            Talk,
            title="Old Title",
            abstract="Old abstract",
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
            event=event,
        )
        original_updated_at = existing_talk.updated_at

        ctx = _ctx(
            log_fn=command._log,
            detect_only=True,
            skip_images=True,
            pretalx_event_url=pretalx_url,
            event_obj=event,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "detected"
        existing_talk.refresh_from_db()
        # Talk row untouched.
        assert existing_talk.title == "Old Title"
        assert existing_talk.updated_at == original_updated_at

        change = PendingPretalxChange.objects.get()
        assert change.kind == PendingPretalxChange.Kind.UPDATE
        assert change.talk == existing_talk
        assert "title" in change.field_diffs
        assert change.field_diffs["title"]["new"] == "Test Talk Title"

    def test_detect_does_not_create_rooms(
        self,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """Detect-only must not touch the Room table while building the field diff."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        mock_submission.state = State.confirmed
        pretalx_url = "https://pretalx.com/evt"
        # Existing talk has no room; the submission references "Main Hall", which is not
        # yet in the DB. Resolving it for the diff must not write a Room row.
        baker.make(
            Talk,
            title="Old Title",
            room=None,
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
            event=event,
        )
        assert Room.objects.count() == 0

        ctx = _ctx(
            log_fn=command._log,
            detect_only=True,
            skip_images=True,
            pretalx_event_url=pretalx_url,
            event_obj=event,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "detected"
        assert Room.objects.count() == 0
        # The room change is still recorded in the diff for the reviewer.
        change = PendingPretalxChange.objects.get()
        assert "room" in change.field_diffs
        assert change.field_diffs["room"]["new"] == "Main Hall"

    def test_detect_no_changes_yields_unchanged(
        self,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """When the local Talk already matches Pretalx, no pending row is written."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        mock_submission.state = State.confirmed
        pretalx_url = "https://pretalx.com/evt"
        room = Room.objects.create(name="Main Hall", event=event)
        speaker1 = Speaker.objects.create(name="John Cleese", pretalx_id="SPK001")
        speaker2 = Speaker.objects.create(name="Eric Idle", pretalx_id="SPK002")

        # Build a Talk that matches the mock_submission exactly.
        talk = Talk.objects.create(
            presentation_type="Talk",
            title=mock_submission.title,
            abstract=mock_submission.abstract,
            description=mock_submission.description,
            start_time=mock_submission.slots[0].start,
            duration=timedelta(minutes=mock_submission.duration),
            room=room,
            track="Data Science",
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
            event=event,
        )
        talk.speakers.add(speaker1, speaker2)

        ctx = _ctx(
            log_fn=command._log,
            detect_only=True,
            skip_images=True,
            pretalx_event_url=pretalx_url,
            event_obj=event,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "unchanged"
        assert PendingPretalxChange.objects.count() == 0

    def test_detect_cancelled_writes_delete_pending(
        self,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """A submission whose state is no longer confirmed/accepted produces a DELETE row."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        mock_submission.state = State.rejected
        pretalx_url = "https://pretalx.com/evt"
        existing_talk = baker.make(
            Talk,
            title="Doomed Talk",
            pretalx_link=f"{pretalx_url}/talk/{mock_submission.code}",
            event=event,
        )

        ctx = _ctx(
            log_fn=command._log,
            detect_only=True,
            skip_images=True,
            pretalx_event_url=pretalx_url,
            event_obj=event,
        )

        result = command._process_single_submission(mock_submission, ctx)

        assert result == "detected"
        assert Talk.objects.filter(pk=existing_talk.pk).exists()  # not deleted
        change = PendingPretalxChange.objects.get()
        assert change.kind == PendingPretalxChange.Kind.DELETE
        assert change.talk == existing_talk

    def test_detect_idempotent_reupserts_open_row(
        self,
        command: Command,
        mock_submission: Mock,
    ) -> None:
        """Re-running detect for an unchanged-detection submission reuses the same row."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        mock_submission.state = State.confirmed
        pretalx_url = "https://pretalx.com/evt"

        ctx = _ctx(
            log_fn=command._log,
            detect_only=True,
            skip_images=True,
            pretalx_event_url=pretalx_url,
            event_obj=event,
        )

        command._process_single_submission(mock_submission, ctx)
        command._process_single_submission(mock_submission, ctx)

        assert PendingPretalxChange.objects.count() == 1


@pytest.mark.django_db
class TestRecordPendingChangeRace:
    """``record_pending_change`` survives a concurrent insert of the same open row."""

    def test_recovers_from_concurrent_insert(self) -> None:
        """An IntegrityError on create falls back to updating the row the other run made."""
        event = Event.objects.create(slug="evt", name="Evt", year=2099)
        # The row a concurrent detect run committed between our lookup and our insert.
        concurrent = PendingPretalxChange.objects.create(
            event=event,
            pretalx_code="ABC123",
            kind=PendingPretalxChange.Kind.CREATE,
            field_diffs={},
            speaker_diffs={"added": [], "removed": []},
            pretalx_payload={"title": "stale"},
        )

        # First lookup misses (we haven't seen the row yet); create hits the partial
        # unique constraint; the recovery lookup finds the concurrent row.
        with (
            patch.object(pending_mod, "_find_open_change", side_effect=[None, concurrent]),
            patch.object(
                PendingPretalxChange.objects,
                "create",
                side_effect=IntegrityError("duplicate open row"),
            ),
        ):
            change, created = record_pending_change(
                event=event,
                pretalx_code="ABC123",
                kind=PendingPretalxChange.Kind.UPDATE,
                talk=None,
                field_diffs={"title": {"old": "stale", "new": "fresh"}},
                speaker_diffs={"added": [], "removed": []},
                pretalx_payload={"title": "fresh"},
            )

        assert created is False
        assert change.pk == concurrent.pk
        concurrent.refresh_from_db()
        assert concurrent.kind == PendingPretalxChange.Kind.UPDATE
        assert concurrent.field_diffs == {"title": {"old": "stale", "new": "fresh"}}

    def test_propagates_when_no_conflicting_row(self) -> None:
        """A genuine IntegrityError with no open row to recover propagates."""
        event = Event.objects.create(slug="evt2", name="Evt2", year=2099)
        with (
            patch.object(pending_mod, "_find_open_change", side_effect=[None, None]),
            patch.object(
                PendingPretalxChange.objects,
                "create",
                side_effect=IntegrityError("boom"),
            ),
            pytest.raises(IntegrityError),
        ):
            record_pending_change(
                event=event,
                pretalx_code="ZZZ",
                kind=PendingPretalxChange.Kind.CREATE,
                talk=None,
                field_diffs={},
                speaker_diffs={"added": [], "removed": []},
                pretalx_payload={},
            )
