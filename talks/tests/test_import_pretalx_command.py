"""
Unit tests for the import_pretalx_talks management command.

Tests cover the core logic without making actual API calls.
"""
# ruff: noqa: SLF001, PLR2004, ARG002
# mypy: disable-error-code="arg-type"

from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import Mock, patch

import pytest
from model_bakery import baker
from pytanis.pretalx.models import State

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
from talks.management.commands._pretalx.types import VerbosityLevel
from talks.management.commands.import_pretalx_talks import Command
from talks.models import FAR_FUTURE, MAX_TALK_TITLE_LENGTH, Room, Speaker, Talk


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
            "pyconde2024",
            pretalx_base_url="https://pretalx.com",
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
            "pyconde2024",
            pretalx_base_url="https://custom.pretalx.com/",
        )

        assert data.pretalx_link == "https://custom.pretalx.com/pyconde2024/talk/ABC123"

    def test_trailing_slash_handling(self, mock_submission: Mock) -> None:
        """Test that trailing slash is handled correctly."""
        data = SubmissionData(
            mock_submission,
            "pyconde2024",
            pretalx_base_url="https://pretalx.com///",
        )

        assert data.pretalx_link == "https://pretalx.com/pyconde2024/talk/ABC123"

    def test_missing_room(self, mock_submission: Mock) -> None:
        """Test handling of submission without room."""
        mock_submission.slots = []
        data = SubmissionData(mock_submission, "pyconde2024")

        assert data.room == ""

    def test_missing_track(self, mock_submission: Mock) -> None:
        """Test handling of submission without track."""
        mock_submission.track = None
        data = SubmissionData(mock_submission, "pyconde2024")

        assert data.track == ""

    def test_title_truncation(self, mock_submission: Mock) -> None:
        """Test that long titles are truncated."""
        mock_submission.title = "A" * (MAX_TALK_TITLE_LENGTH + 50)
        data = SubmissionData(mock_submission, "pyconde2024")

        assert len(data.title) == MAX_TALK_TITLE_LENGTH

    def test_duration_extraction(self, mock_submission: Mock) -> None:
        """Test that duration is extracted as timedelta."""
        data = SubmissionData(mock_submission, "pyconde2024")

        assert data.duration == timedelta(minutes=45)

    def test_missing_duration(self, mock_submission: Mock) -> None:
        """Test handling of submission without duration."""
        mock_submission.duration = None
        data = SubmissionData(mock_submission, "pyconde2024")

        assert data.duration is None

    def test_start_time_extraction(self, mock_submission: Mock) -> None:
        """Test that start time is extracted from slots."""
        data = SubmissionData(mock_submission, "pyconde2024")

        assert data.start_time == datetime(2024, 6, 15, 10, 0, tzinfo=UTC)

    def test_missing_start_time(self, mock_submission: Mock) -> None:
        """Test handling of submission without start time."""
        mock_submission.slots[0].start = None
        data = SubmissionData(mock_submission, "pyconde2024")

        assert data.start_time == FAR_FUTURE

    def test_empty_title_handling(self, mock_submission: Mock) -> None:
        """Test handling of empty title."""
        mock_submission.title = None
        data = SubmissionData(mock_submission, "pyconde2024")

        assert data.title == ""


# ---------------------- _is_valid_submission Tests ----------------------


@pytest.mark.django_db
class TestIsValidSubmission:
    """Tests for the _is_valid_submission method."""

    def test_valid_submission(self, command: Command, mock_submission: Mock) -> None:
        """Test that valid submission passes validation."""
        result = command._is_valid_submission(mock_submission, VerbosityLevel.NORMAL)

        assert result is True

    def test_missing_title(self, command: Command, mock_submission: Mock) -> None:
        """Test that submission without title fails validation."""
        mock_submission.title = None

        result = command._is_valid_submission(mock_submission, VerbosityLevel.NORMAL)

        assert result is False

    @patch("talks.management.commands.import_pretalx_talks.settings")
    def test_missing_speakers_regular_talk(
        self,
        mock_settings: Mock,
        command: Command,
        mock_submission_no_speakers: Mock,
    ) -> None:
        """Test that submission without speakers follows IMPORT_TALKS_WITHOUT_SPEAKERS setting."""
        mock_settings.IMPORT_TALKS_WITHOUT_SPEAKERS = False
        mock_submission_no_speakers.submission_type.en = "Talk"

        result = command._is_valid_submission(
            mock_submission_no_speakers,
            VerbosityLevel.NORMAL,
        )

        assert result is False

    def test_lightning_talk_without_speakers(
        self,
        command: Command,
        mock_submission_lightning: Mock,
    ) -> None:
        """Test that Lightning Talks are allowed without speakers."""
        result = command._is_valid_submission(mock_submission_lightning, VerbosityLevel.NORMAL)

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
        command: Command,
        input_type: str,
        expected: str,
    ) -> None:
        """Test mapping of known presentation types."""
        result = command._map_presentation_type(input_type, "ABC123", VerbosityLevel.NORMAL)

        assert result == expected

    def test_unknown_type_defaults_to_talk(self, command: Command) -> None:
        """Test that unknown types default to Talk."""
        result = command._map_presentation_type(
            "Unknown Type",
            "ABC123",
            VerbosityLevel.NORMAL,
        )

        assert result == Talk.PresentationType.TALK

    def test_empty_type_defaults_to_talk(self, command: Command) -> None:
        """Test that empty/None types default to Talk."""
        result = command._map_presentation_type("", "ABC123", VerbosityLevel.NORMAL)
        assert result == Talk.PresentationType.TALK

        result = command._map_presentation_type(None, "ABC123", VerbosityLevel.NORMAL)
        assert result == Talk.PresentationType.TALK


# ---------------------- batch_create_rooms Tests ----------------------


@pytest.mark.django_db
class TestBatchCreateRooms:
    """Tests for the batch_create_rooms function."""

    def test_creates_new_rooms(self, mock_submission: Mock) -> None:
        """Test that new rooms are created via bulk_create."""
        mock_submission.state = State.confirmed
        submissions = [mock_submission]
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value}

        batch_create_rooms(submissions, "pyconde2024", options)

        assert Room.objects.filter(name="Main Hall").exists()

    def test_skips_existing_rooms(self, mock_submission: Mock) -> None:
        """Test that existing rooms are not recreated."""
        # Create existing room
        Room.objects.create(name="Main Hall", description="Original description")

        mock_submission.state = State.confirmed
        submissions = [mock_submission]
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value}

        batch_create_rooms(submissions, "pyconde2024", options)

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
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value}

        batch_create_rooms(submissions, "pyconde2024", options)

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
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value}

        batch_create_rooms(submissions, "pyconde2024", options)

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
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value}

        batch_create_or_update_speakers(submissions, options)

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
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value, "no_update": False}

        batch_create_or_update_speakers(submissions, options)

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
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value, "no_update": True}

        batch_create_or_update_speakers(submissions, options)

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
        options: dict[str, Any] = {"verbosity": VerbosityLevel.NORMAL.value}

        batch_create_or_update_speakers(submissions, options)

        # SPK001 should only be created once
        assert Speaker.objects.filter(pretalx_id="SPK001").count() == 1


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

    @patch.object(Command, "_create_talk")
    @patch.object(Command, "_add_speakers_to_talk")
    @patch.object(Command, "_generate_talk_image")
    def test_creates_new_talk(
        self,
        mock_update_image: Mock,
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

        # Mock _create_talk to return a Talk
        talk = baker.make(Talk, title="Test Talk", room=room)
        mock_create_talk.return_value = talk

        options: dict[str, Any] = {
            "verbosity": VerbosityLevel.NORMAL.value,
            "dry_run": False,
            "no_update": False,
            "skip_images": True,
            "pretalx_base_url": "https://pretalx.com",
        }

        result = command._process_single_submission(mock_submission, "pyconde2024", options)

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

        options: dict[str, Any] = {
            "verbosity": VerbosityLevel.NORMAL.value,
            "dry_run": True,
            "no_update": False,
            "skip_images": True,
            "pretalx_base_url": "https://pretalx.com",
        }

        result = command._process_single_submission(mock_submission, "pyconde2024", options)

        # Returns "created" to indicate what would happen, but no DB changes
        assert result == "created"
        assert Talk.objects.count() == 0
