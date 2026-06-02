"""Unit tests for the import_livestream_urls management command."""
# ruff: noqa: DTZ001 PLR2004

from datetime import datetime
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import httpx2
import pandas as pd
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from model_bakery import baker

from events.models import Event
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

    def test_scoped_to_event(self, command: Command) -> None:
        """With an event, a same-named room is resolved within that event only."""
        event_a = Event.objects.create(slug="a", name="A", year=2025)
        event_b = Event.objects.create(slug="b", name="B", year=2026)
        room_a = Room.objects.create(name="Titanium", event=event_a)
        Room.objects.create(name="Titanium", event=event_b)
        assert command.get_room("Titanium", event_a) == room_a


# ---------------------------------------------------------------------------
# fetch_spreadsheet_data
# ---------------------------------------------------------------------------
class TestFetchSpreadsheetData:
    """Verify fetch_spreadsheet_data reads, filters, and normalizes spreadsheet data."""

    @patch("pandas.read_excel")
    @patch("httpx2.get")
    def test_success(self, mock_get: MagicMock, mock_read: MagicMock, command: Command) -> None:
        """Parse the spreadsheet and return a DataFrame with expected columns."""
        mock_response = MagicMock(spec=httpx2.Response)
        mock_response.content = b"fake"
        mock_get.return_value = mock_response
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
    @patch("httpx2.get")
    def test_filters_non_vimeo(
        self,
        mock_get: MagicMock,
        mock_read: MagicMock,
        command: Command,
    ) -> None:
        """Exclude rows where the streaming platform is not Vimeo."""
        mock_response = MagicMock(spec=httpx2.Response)
        mock_response.content = b"fake"
        mock_get.return_value = mock_response
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

    @patch("httpx2.get")
    def test_raises_on_error(self, mock_get: MagicMock, command: Command) -> None:
        """Propagate exceptions from the underlying HTTP request."""
        mock_get.side_effect = httpx2.HTTPError("Network error")
        with pytest.raises(httpx2.HTTPError, match="Network error"):
            command.fetch_spreadsheet_data("sheet-id", "Sheet1")


# ---------------------------------------------------------------------------
# handle
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestHandleCommand:
    """End-to-end tests for handle(), verifying import, dry-run, and clearing logic."""

    @pytest.fixture(autouse=True)
    def _no_default_event(self, settings: Any) -> None:
        """Disable DEFAULT_EVENT so the command doesn't look for a non-existent event."""
        settings.DEFAULT_EVENT = ""

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

    def test_unknown_event_slug_aborts_without_deleting(
        self,
        sample_dataframe: pd.DataFrame,
    ) -> None:
        """A configured-but-unknown --event-slug aborts instead of wiping all streamings."""
        room = baker.make(Room, name="Titanium")
        baker.make(
            Streaming,
            room=room,
            start_time=sample_dataframe["Start Time"].iloc[0],
            end_time=sample_dataframe["End Time"].iloc[0],
            video_link="https://old.link",
        )
        with pytest.raises(CommandError, match="not found"):
            call_command(
                "import_livestream_urls",
                "--event-slug=does-not-exist",
                "--livestreams-sheet-id=x",
                "--livestreams-worksheet-name=y",
            )
        # The destructive delete must not have run.
        assert Streaming.objects.count() == 1

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
