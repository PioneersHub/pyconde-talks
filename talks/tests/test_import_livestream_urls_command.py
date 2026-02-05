"""Unit tests for the import_livestream_urls management command."""
# ruff: noqa: DTZ001 PLR2004

from datetime import datetime
from io import StringIO
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from django.core.management import call_command
from model_bakery import baker

from talks.management.commands.import_livestream_urls import Command
from talks.models import Room, Streaming


@pytest.fixture()
def command() -> Command:
    """Create a Command instance with mocked stdout/stderr."""
    cmd = Command()
    cmd.stdout = StringIO()  # type: ignore[assignment]
    cmd.stderr = StringIO()  # type: ignore[assignment]
    return cmd


@pytest.fixture()
def sample_dataframe() -> pd.DataFrame:
    """Create a sample DataFrame matching spreadsheet format."""
    return pd.DataFrame(
        {
            "Room": ["Titanium", "Helium", "Nonexistent"],
            "Start Time": pd.to_datetime(
                ["2025-06-01 09:00", "2025-06-01 10:00", "2025-06-01 11:00"],
            ).tz_localize("Europe/Berlin"),
            "End Time": pd.to_datetime(
                ["2025-06-01 12:00", "2025-06-01 13:00", "2025-06-01 14:00"],
            ).tz_localize("Europe/Berlin"),
            "Embed Link": [
                "https://youtube.com/live1",
                "https://youtube.com/live2",
                "https://youtube.com/live3",
            ],
            "Vimeo / Restream": ["Vimeo", "Vimeo", "Vimeo"],
        },
    )


# ---------------------------------------------------------------------------
# get_room
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestGetRoom:
    """Verify get_room resolves room names, handles whitespace, and returns None for unknowns."""

    def test_existing_room(self, command: Command) -> None:
        """Return the Room object when a room with a matching name exists."""
        room = baker.make(Room, name="Titanium")
        result = command.get_room("Titanium")
        assert result == room

    def test_room_with_whitespace(self, command: Command) -> None:
        """Strip leading and trailing whitespace before looking up the room."""
        baker.make(Room, name="Titanium")
        result = command.get_room("  Titanium  ")
        assert result is not None
        assert result.name == "Titanium"

    def test_nonexistent_room(self, command: Command) -> None:
        """Return None when no room matches the given name."""
        result = command.get_room("FakeRoom")
        assert result is None


# ---------------------------------------------------------------------------
# fetch_spreadsheet_data
# ---------------------------------------------------------------------------
class TestFetchSpreadsheetData:
    """Verify fetch_spreadsheet_data reads, filters, and normalizes spreadsheet data."""

    @patch("pandas.read_excel")
    def test_success(self, mock_read: MagicMock, command: Command) -> None:
        """Parse the spreadsheet and return a DataFrame with expected columns."""
        raw_df = pd.DataFrame(
            {
                "Room": ["Titanium"],
                "Start Time": [datetime(2025, 6, 1, 9, 0)],
                "End Time": [datetime(2025, 6, 1, 12, 0)],
                "Embed Link": ["https://youtube.com/live"],
                "Vimeo / Restream": ["Vimeo"],
            },
        )
        mock_read.return_value = raw_df
        result = command.fetch_spreadsheet_data("sheet-id", "Sheet1")
        assert len(result) == 1
        assert "Room" in result.columns

    @patch("pandas.read_excel")
    def test_filters_non_vimeo(self, mock_read: MagicMock, command: Command) -> None:
        """Exclude rows where the streaming platform is not Vimeo."""
        raw_df = pd.DataFrame(
            {
                "Room": ["Titanium", "Helium"],
                "Start Time": [datetime(2025, 6, 1, 9, 0), datetime(2025, 6, 1, 10, 0)],
                "End Time": [datetime(2025, 6, 1, 12, 0), datetime(2025, 6, 1, 13, 0)],
                "Embed Link": ["https://youtube.com/live1", "https://youtube.com/live2"],
                "Vimeo / Restream": ["Vimeo", "Restream"],
            },
        )
        mock_read.return_value = raw_df
        result = command.fetch_spreadsheet_data("sheet-id", "Sheet1")
        assert len(result) == 1

    @patch("pandas.read_excel")
    def test_raises_on_error(self, mock_read: MagicMock, command: Command) -> None:
        """Propagate exceptions from the underlying spreadsheet reader."""
        mock_read.side_effect = Exception("Network error")
        with pytest.raises(Exception, match="Network error"):
            command.fetch_spreadsheet_data("sheet-id", "Sheet1")


# ---------------------------------------------------------------------------
# handle
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestHandleCommand:
    """End-to-end tests for handle(), verifying import, dry-run, and clearing logic."""

    @patch.object(Command, "fetch_spreadsheet_data")
    def test_import_creates_streamings(
        self,
        mock_fetch: MagicMock,
        sample_dataframe: pd.DataFrame,
    ) -> None:
        """Create Streaming objects for matched rooms and skip unrecognized room names."""
        baker.make(Room, name="Titanium")
        baker.make(Room, name="Helium")
        mock_fetch.return_value = sample_dataframe

        stdout = StringIO()
        call_command(
            "import_livestream_urls",
            "--livestreams-sheet-id=test-id",
            "--livestreams-worksheet-name=Sheet1",
            stdout=stdout,
        )
        output = stdout.getvalue()
        # 2 rooms found, 1 skipped (Nonexistent)
        assert Streaming.objects.count() == 2
        assert "skipped: 1" in output

    @patch.object(Command, "fetch_spreadsheet_data")
    def test_dry_run_no_db_changes(
        self,
        mock_fetch: MagicMock,
        sample_dataframe: pd.DataFrame,
    ) -> None:
        """Leave the database unchanged when --dry-run is passed."""
        baker.make(Room, name="Titanium")
        baker.make(Room, name="Helium")
        mock_fetch.return_value = sample_dataframe

        stdout = StringIO()
        call_command(
            "import_livestream_urls",
            "--livestreams-sheet-id=test-id",
            "--livestreams-worksheet-name=Sheet1",
            "--dry-run",
            stdout=stdout,
        )
        assert Streaming.objects.count() == 0
        output = stdout.getvalue()
        assert "DRY RUN" in output

    @patch.object(Command, "fetch_spreadsheet_data")
    def test_clears_existing_streamings(
        self,
        mock_fetch: MagicMock,
        sample_dataframe: pd.DataFrame,
    ) -> None:
        """Delete pre-existing streaming sessions before importing new ones."""
        room = baker.make(Room, name="Titanium")
        baker.make(Room, name="Helium")
        baker.make(
            Streaming,
            room=room,
            start_time=sample_dataframe["Start Time"].iloc[0],
            end_time=sample_dataframe["End Time"].iloc[0],
            video_link="https://old.link",
        )
        assert Streaming.objects.count() == 1

        mock_fetch.return_value = sample_dataframe
        stdout = StringIO()
        call_command(
            "import_livestream_urls",
            "--livestreams-sheet-id=test-id",
            "--livestreams-worksheet-name=Sheet1",
            stdout=stdout,
        )
        output = stdout.getvalue()
        assert "Deleted" in output

    @patch.object(Command, "fetch_spreadsheet_data")
    def test_command_failure_raises(self, mock_fetch: MagicMock) -> None:
        """Propagate errors from fetch_spreadsheet_data up to the caller."""
        mock_fetch.side_effect = RuntimeError("Sheet not found")
        with pytest.raises(RuntimeError, match="Sheet not found"):
            call_command(
                "import_livestream_urls",
                "--livestreams-sheet-id=bad",
                "--livestreams-worksheet-name=bad",
            )
