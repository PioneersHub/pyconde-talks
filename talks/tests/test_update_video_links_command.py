"""Unit tests for the update_video_links management command."""
# ruff: noqa: PLR2004

from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.core.management import call_command
from model_bakery import baker

from talks.management.commands.update_video_links import Command
from talks.models import Talk


@pytest.fixture()
def command() -> Command:
    """Create a Command instance with mocked stdout/stderr."""
    cmd = Command()
    cmd.stdout = StringIO()  # type: ignore[assignment]
    cmd.stderr = StringIO()  # type: ignore[assignment]
    return cmd


VIMEO_RESPONSE: dict[str, Any] = {
    "data": [
        {"name": "ABC123_My Talk Title", "player_embed_url": "https://player.vimeo.com/video/111"},
        {"name": "DEF456_Another Talk", "player_embed_url": "https://player.vimeo.com/video/222"},
    ],
}


# ---------------------------------------------------------------------------
# fetch_single_folder
# ---------------------------------------------------------------------------
class TestFetchSingleFolder:
    """Verify fetch_single_folder parses Vimeo API responses into name-to-URL maps."""

    @patch("httpx.get")
    def test_returns_name_to_url_mapping(
        self,
        mock_get: MagicMock,
        command: Command,
    ) -> None:
        """Map each video name to its embed URL from the Vimeo API response."""
        mock_response = MagicMock()
        mock_response.json.return_value = VIMEO_RESPONSE
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = command.fetch_single_folder("token", "proj1")
        assert result == {
            "ABC123_My Talk Title": "https://player.vimeo.com/video/111",
            "DEF456_Another Talk": "https://player.vimeo.com/video/222",
        }

    @patch("httpx.get")
    def test_empty_folder(self, mock_get: MagicMock, command: Command) -> None:
        """Return an empty dict when the Vimeo folder contains no videos."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = command.fetch_single_folder("token", "proj1")
        assert result == {}

    @patch("httpx.get")
    def test_raises_on_http_error(self, mock_get: MagicMock, command: Command) -> None:
        """Propagate HTTP errors from the Vimeo API."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401",
            request=MagicMock(),
            response=MagicMock(),
        )
        mock_get.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            command.fetch_single_folder("bad-token", "proj1")


# ---------------------------------------------------------------------------
# fetch_vimeo_data
# ---------------------------------------------------------------------------
class TestFetchVimeoData:
    """Verify fetch_vimeo_data aggregates results from multiple project folders."""

    @patch.object(Command, "fetch_single_folder")
    def test_combines_multiple_folders(
        self,
        mock_fetch_folder: MagicMock,
        command: Command,
    ) -> None:
        """Merge video data from multiple Vimeo project folders into one dict."""
        mock_fetch_folder.side_effect = [
            {"ABC_Talk1": "https://vimeo.com/1"},
            {"DEF_Talk2": "https://vimeo.com/2"},
        ]
        result = command.fetch_vimeo_data("token", ["proj1", "proj2"])
        assert len(result) == 2
        assert "ABC_Talk1" in result
        assert "DEF_Talk2" in result

    @patch.object(Command, "fetch_single_folder")
    def test_single_folder(self, mock_fetch_folder: MagicMock, command: Command) -> None:
        """Return the data from a single folder without merging."""
        mock_fetch_folder.return_value = {"ABC_Talk1": "https://vimeo.com/1"}
        result = command.fetch_vimeo_data("token", ["proj1"])
        assert result == {"ABC_Talk1": "https://vimeo.com/1"}


# ---------------------------------------------------------------------------
# update_video_links
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestUpdateVideoLinks:
    """Verify update_video_links matches Vimeo video names to talks by pretalx ID."""

    def test_updates_matching_talk(self, command: Command) -> None:
        """Set the video_link and reset video_start_time for a matching talk."""
        talk = baker.make(
            Talk,
            pretalx_link="https://pretalx.com/pyconde/talk/ABC123/",
            video_link="",
        )
        vimeo_data = {"ABC123_Talk Title": "https://player.vimeo.com/video/111"}
        command.update_video_links(vimeo_data)
        talk.refresh_from_db()
        assert talk.video_link == "https://player.vimeo.com/video/111"
        assert talk.video_start_time == 0

    def test_no_matching_talk_logs_warning(self, command: Command) -> None:
        """Log a warning when no talk matches the pretalx ID from the video name."""
        vimeo_data = {"MISSING_Talk": "https://player.vimeo.com/video/999"}
        command.update_video_links(vimeo_data)
        output = command.stdout.getvalue()  # type: ignore[union-attr]
        assert "Talk not found" in output

    def test_multiple_talks_updated(self, command: Command) -> None:
        """Update video links for multiple talks in a single batch."""
        t1 = baker.make(Talk, pretalx_link="https://pretalx.com/t/ABC123/", video_link="")
        t2 = baker.make(Talk, pretalx_link="https://pretalx.com/t/DEF456/", video_link="")
        vimeo_data = {
            "ABC123_Talk 1": "https://vimeo.com/1",
            "DEF456_Talk 2": "https://vimeo.com/2",
        }
        command.update_video_links(vimeo_data)
        t1.refresh_from_db()
        t2.refresh_from_db()
        assert t1.video_link == "https://vimeo.com/1"
        assert t2.video_link == "https://vimeo.com/2"


# ---------------------------------------------------------------------------
# handle (integration)  # noqa: ERA001
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestHandleCommand:
    """End-to-end tests for handle(), verifying talk updates, dry-run, and errors."""

    @patch.object(Command, "fetch_vimeo_data")
    def test_updates_talks(self, mock_fetch: MagicMock) -> None:
        """Fetch from Vimeo and update matching talks' video links."""
        talk = baker.make(
            Talk,
            pretalx_link="https://pretalx.com/t/ABC123/",
            video_link="",
        )
        mock_fetch.return_value = {"ABC123_Talk": "https://vimeo.com/1"}

        stdout = StringIO()
        call_command(
            "update_video_links",
            "--vimeo-access-token=tok",
            "--vimeo-project-ids=proj1",
            stdout=stdout,
        )
        talk.refresh_from_db()
        assert talk.video_link == "https://vimeo.com/1"
        assert "Successfully updated" in stdout.getvalue()

    @patch.object(Command, "fetch_vimeo_data")
    def test_dry_run_no_db_changes(self, mock_fetch: MagicMock) -> None:
        """Leave the database unchanged when --dry-run is passed."""
        talk = baker.make(
            Talk,
            pretalx_link="https://pretalx.com/t/ABC123/",
            video_link="",
        )
        mock_fetch.return_value = {"ABC123_Talk": "https://vimeo.com/1"}

        stdout = StringIO()
        call_command(
            "update_video_links",
            "--vimeo-access-token=tok",
            "--vimeo-project-ids=proj1",
            "--dry-run",
            stdout=stdout,
        )
        talk.refresh_from_db()
        assert talk.video_link == ""
        output = stdout.getvalue()
        assert "DRY RUN" in output

    @patch.object(Command, "fetch_vimeo_data")
    def test_command_failure_raises(self, mock_fetch: MagicMock) -> None:
        """Propagate API errors up to the caller."""
        mock_fetch.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            call_command(
                "update_video_links",
                "--vimeo-access-token=tok",
                "--vimeo-project-ids=proj1",
            )
