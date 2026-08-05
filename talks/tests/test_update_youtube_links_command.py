"""Unit tests for the update_youtube_links management command."""
# ruff: noqa: PLR2004

import json
from datetime import timedelta
from io import StringIO
from typing import TYPE_CHECKING, Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.management.commands.update_youtube_links import Command, UpdatePlan
from talks.models import Talk


if TYPE_CHECKING:
    from pathlib import Path

    from pytest_django.fixtures import SettingsWrapper


# Stand-in Pretalx submission codes, shaped like the real ones.
# cspell:ignore KFPNUA KFPNUAXYZ LVRLSU HKFCBM NOSUCH

VIDEO_ID = "Z7Xlj2eG8sc"
OTHER_VIDEO_ID = "08z826ZYvKI"


@pytest.fixture
def command() -> Command:
    """Create a Command instance with mocked stdout/stderr."""
    cmd = Command()
    cmd.stdout = StringIO()  # type: ignore[assignment]
    cmd.stderr = StringIO()  # type: ignore[assignment]
    return cmd


@pytest.fixture
def json_map(tmp_path: Path) -> Path:
    """Write a one-entry Pretalx code to YouTube ID map and return its path."""
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"KFPNUA": VIDEO_ID}), encoding="utf-8")
    return path


def make_talk(code: str, **kwargs: Any) -> Talk:
    """Create a talk whose Pretalx link carries *code*."""
    kwargs.setdefault("video_link", "")
    talk: Talk = baker.make(
        Talk,
        pretalx_link=f"https://pretalx.com/pyconde-pydata-2026/talk/{code}/",
        **kwargs,
    )
    return talk


# ---------------------------------------------------------------------------
# load_map
# ---------------------------------------------------------------------------
class TestLoadMap:
    """Verify load_map parses the JSON file and rejects unusable input."""

    def test_reads_mapping(self, command: Command, json_map: Path) -> None:
        """Return the code-to-ID mapping from the file."""
        assert command.load_map(json_map) == {"KFPNUA": VIDEO_ID}

    def test_strips_whitespace(self, command: Command, tmp_path: Path) -> None:
        """Trim padding around codes and IDs so a hand-edited map still matches."""
        path = tmp_path / "padded.json"
        path.write_text(json.dumps({" KFPNUA ": f" {VIDEO_ID} "}), encoding="utf-8")
        assert command.load_map(path) == {"KFPNUA": VIDEO_ID}

    def test_missing_file(self, command: Command, tmp_path: Path) -> None:
        """Abort when the file does not exist."""
        with pytest.raises(CommandError, match="Could not read"):
            command.load_map(tmp_path / "nope.json")

    def test_invalid_json(self, command: Command, tmp_path: Path) -> None:
        """Abort when the file is not valid JSON."""
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(CommandError, match="not valid JSON"):
            command.load_map(path)

    def test_not_an_object(self, command: Command, tmp_path: Path) -> None:
        """Abort when the JSON is not an object of code-to-ID pairs."""
        path = tmp_path / "list.json"
        path.write_text(json.dumps(["KFPNUA"]), encoding="utf-8")
        with pytest.raises(CommandError, match="must contain a JSON object"):
            command.load_map(path)


# ---------------------------------------------------------------------------
# resolve_event
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestResolveEvent:
    """Verify resolve_event scopes the run and rejects unknown slugs."""

    def test_known_slug(self, command: Command) -> None:
        """Return the event matching the slug."""
        event = baker.make(Event, slug="pyconde-2026")
        assert command.resolve_event("pyconde-2026") == event

    def test_empty_slug_is_unscoped(self, command: Command) -> None:
        """Return None when no slug is given, meaning talks of every event."""
        assert command.resolve_event("") is None

    def test_unknown_slug_aborts(self, command: Command) -> None:
        """Abort on a slug that does not resolve rather than updating every event."""
        with pytest.raises(CommandError, match="not found"):
            command.resolve_event("typo")


# ---------------------------------------------------------------------------
# talks_by_code
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTalksByCode:
    """Verify talks_by_code groups talks by their parsed Pretalx code."""

    def test_groups_by_code(self, command: Command) -> None:
        """Index each talk under the code parsed from its Pretalx link."""
        talk = make_talk("KFPNUA")
        assert command.talks_by_code(None) == {"KFPNUA": [talk]}

    def test_skips_talks_without_pretalx_link(self, command: Command) -> None:
        """Leave out talks that have no Pretalx link, since they can never match."""
        make_talk("KFPNUA")
        baker.make(Talk, pretalx_link="")
        assert list(command.talks_by_code(None)) == ["KFPNUA"]

    def test_scoped_to_event(self, command: Command) -> None:
        """Only include talks of the given event."""
        event = baker.make(Event, slug="wanted")
        mine = make_talk("KFPNUA", event=event)
        make_talk("LVRLSU")
        assert command.talks_by_code(event) == {"KFPNUA": [mine]}


# ---------------------------------------------------------------------------
# update_video_links
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestUpdateVideoLinks:
    """Verify update_video_links writes the right link and reports every outcome."""

    def test_updates_matching_talk(self, command: Command) -> None:
        """Store the short YouTube URL and reset the start time."""
        talk = make_talk("KFPNUA", video_start_time=42)
        stats = command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        talk.refresh_from_db()
        # save() enriches YouTube links with enablejsapi=1 for the IFrame API.
        assert talk.video_link == f"https://youtu.be/{VIDEO_ID}?enablejsapi=1"
        assert talk.video_start_time == 0
        assert stats.updated == 1

    def test_stored_link_is_playable(self, command: Command) -> None:
        """The short URL that lands in the database renders as an embeddable one."""
        talk = make_talk("KFPNUA", start_time=timezone.now() - timedelta(hours=2))
        command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        talk.refresh_from_db()
        talk.videos_unlocked = True
        assert talk.get_video_link() == f"https://www.youtube.com/embed/{VIDEO_ID}?enablejsapi=1"

    def test_rerun_is_a_noop(self, command: Command) -> None:
        """A second run over the same map changes nothing, whatever the stored URL form."""
        make_talk("KFPNUA", video_link=f"https://www.youtube.com/watch?v={VIDEO_ID}")
        stats = command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        assert stats.unchanged == 1
        assert stats.updated == 0

    def test_replaces_a_different_link(self, command: Command) -> None:
        """Overwrite a link pointing at another video, such as a Vimeo rough cut."""
        talk = make_talk("KFPNUA", video_link="https://vimeo.com/123456789")
        command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        talk.refresh_from_db()
        assert talk.video_link == f"https://youtu.be/{VIDEO_ID}?enablejsapi=1"

    def test_skip_existing_keeps_current_link(self, command: Command) -> None:
        """Leave a talk that already has a link untouched under --skip-existing."""
        talk = make_talk("KFPNUA", video_link="https://vimeo.com/123456789")
        stats = command.update_video_links(
            {"KFPNUA": VIDEO_ID},
            command.talks_by_code(None),
            UpdatePlan(skip_existing=True),
        )
        talk.refresh_from_db()
        assert talk.video_link == "https://vimeo.com/123456789"
        assert stats.skipped == 1

    def test_skip_existing_still_fills_empty_links(self, command: Command) -> None:
        """--skip-existing only protects talks that already have a link."""
        talk = make_talk("KFPNUA")
        command.update_video_links(
            {"KFPNUA": VIDEO_ID},
            command.talks_by_code(None),
            UpdatePlan(skip_existing=True),
        )
        talk.refresh_from_db()
        assert VIDEO_ID in talk.video_link

    def test_dry_run_reports_without_writing(self, command: Command) -> None:
        """Count and print the change, but leave the database alone."""
        talk = make_talk("KFPNUA")
        stats = command.update_video_links(
            {"KFPNUA": VIDEO_ID},
            command.talks_by_code(None),
            UpdatePlan(dry_run=True),
        )
        talk.refresh_from_db()
        assert talk.video_link == ""
        assert stats.updated == 1

    def test_exact_code_match_avoids_prefix_collision(self, command: Command) -> None:
        """A code must not clobber a talk whose code merely starts with it."""
        short = make_talk("KFPNUA")
        longer = make_talk("KFPNUAXYZ", video_link="https://vimeo.com/1")
        command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        short.refresh_from_db()
        longer.refresh_from_db()
        assert VIDEO_ID in short.video_link
        assert longer.video_link == "https://vimeo.com/1"

    def test_unknown_code_is_reported(self, command: Command) -> None:
        """Warn and count the codes that match no talk."""
        stats = command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        assert stats.not_found == 1
        assert "no talk with this Pretalx code" in command.stdout.getvalue()  # type: ignore[union-attr]

    def test_ambiguous_code_is_skipped(self, command: Command) -> None:
        """Skip a code shared by several talks instead of picking one at random."""
        first = make_talk("KFPNUA")
        second = make_talk("KFPNUA")
        stats = command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        first.refresh_from_db()
        second.refresh_from_db()
        assert stats.ambiguous == 1
        assert (first.video_link, second.video_link) == ("", "")

    @pytest.mark.parametrize(
        "video_id",
        ["", "too-short", f"https://youtu.be/{VIDEO_ID}", "Z7Xlj2eG8sc!"],
        ids=["empty", "short", "full-url", "bad-character"],
    )
    def test_invalid_video_id_is_rejected(self, command: Command, video_id: str) -> None:
        """Refuse anything that is not a bare 11-character YouTube ID."""
        talk = make_talk("KFPNUA")
        stats = command.update_video_links({"KFPNUA": video_id}, command.talks_by_code(None))
        talk.refresh_from_db()
        assert stats.invalid == 1
        assert talk.video_link == ""

    def test_reports_talks_absent_from_the_map(self, command: Command) -> None:
        """List the still-unlinked talks the map does not cover."""
        make_talk("KFPNUA")
        make_talk("LVRLSU")
        make_talk("HKFCBM", video_link="https://vimeo.com/1")
        stats = command.update_video_links({"KFPNUA": VIDEO_ID}, command.talks_by_code(None))
        assert stats.missing_codes == ["LVRLSU"]

    def test_updates_several_talks(self, command: Command) -> None:
        """Update every code in the map in one pass."""
        first = make_talk("KFPNUA")
        second = make_talk("LVRLSU")
        stats = command.update_video_links(
            {"KFPNUA": VIDEO_ID, "LVRLSU": OTHER_VIDEO_ID},
            command.talks_by_code(None),
        )
        first.refresh_from_db()
        second.refresh_from_db()
        assert stats.updated == 2
        assert VIDEO_ID in first.video_link
        assert OTHER_VIDEO_ID in second.video_link


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestReport:
    """Verify the run summary spells out every non-zero outcome."""

    def test_lists_all_outcomes(self, command: Command) -> None:
        """Mention each problem count, not just the successful updates."""
        make_talk("LVRLSU")
        make_talk("HKFCBM")
        make_talk("Q9HMT3")
        make_talk("Q9HMT3")
        stats = command.update_video_links(
            {"LVRLSU": VIDEO_ID, "NOSUCH": OTHER_VIDEO_ID, "Q9HMT3": VIDEO_ID, "AAAAAA": "bad"},
            command.talks_by_code(None),
        )
        command.report(stats, dry_run=False)
        output = command.stdout.getvalue()  # type: ignore[union-attr]
        assert "1 talk(s) updated" in output
        assert "1 code(s) matched no talk" in output
        assert "1 code(s) matched more than one talk" in output
        assert "1 invalid YouTube ID(s) in the map" in output
        assert "HKFCBM" in output

    def test_truncates_a_long_missing_list(self, command: Command) -> None:
        """Say how many codes are missing rather than printing an unbounded list."""
        codes = [f"CODE{index:02d}" for index in range(25)]
        for code in codes:
            make_talk(code)
        stats = command.update_video_links({}, command.talks_by_code(None))
        command.report(stats, dry_run=False)
        output = command.stdout.getvalue()  # type: ignore[union-attr]
        assert "25 talk(s) still have no recording" in output
        assert "and 5 more" in output

    def test_dry_run_wording(self, command: Command) -> None:
        """Say "would be updated" when nothing was written."""
        make_talk("KFPNUA")
        stats = command.update_video_links(
            {"KFPNUA": VIDEO_ID},
            command.talks_by_code(None),
            UpdatePlan(dry_run=True),
        )
        command.report(stats, dry_run=True)
        assert "1 talk(s) would be updated" in command.stdout.getvalue()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# handle (integration)  # noqa: ERA001
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestHandleCommand:
    """End-to-end tests for handle(), covering scoping, formats, and dry-run."""

    def test_updates_talks(self, json_map: Path) -> None:
        """Read the map and point the matching talk at its recording."""
        talk = make_talk("KFPNUA")
        stdout = StringIO()
        call_command("update_youtube_links", str(json_map), "--event-slug=", stdout=stdout)
        talk.refresh_from_db()
        assert talk.video_link == f"https://youtu.be/{VIDEO_ID}?enablejsapi=1"
        assert "Done" in stdout.getvalue()

    def test_dry_run_makes_no_changes(self, json_map: Path) -> None:
        """Leave the database untouched under --dry-run."""
        talk = make_talk("KFPNUA")
        stdout = StringIO()
        call_command(
            "update_youtube_links",
            str(json_map),
            "--event-slug=",
            "--dry-run",
            stdout=stdout,
        )
        talk.refresh_from_db()
        assert talk.video_link == ""
        output = stdout.getvalue()
        assert "DRY RUN" in output
        assert "Dry run completed" in output

    def test_event_scoping(self, json_map: Path) -> None:
        """Only talks of the given event are updated, even on a shared Pretalx code."""
        wanted = baker.make(Event, slug="wanted")
        mine = make_talk("KFPNUA", event=wanted)
        theirs = make_talk("KFPNUA")
        call_command(
            "update_youtube_links",
            str(json_map),
            "--event-slug=wanted",
            stdout=StringIO(),
        )
        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert VIDEO_ID in mine.video_link
        assert theirs.video_link == ""

    def test_unknown_event_aborts(self, json_map: Path) -> None:
        """A slug that does not resolve stops the run."""
        with pytest.raises(CommandError, match="not found"):
            call_command("update_youtube_links", str(json_map), "--event-slug=typo")

    def test_defaults_to_configured_event(
        self,
        json_map: Path,
        settings: SettingsWrapper,
    ) -> None:
        """With no flag, the run is scoped to DEFAULT_EVENT."""
        settings.DEFAULT_EVENT = "configured"
        event = baker.make(Event, slug="configured")
        mine = make_talk("KFPNUA", event=event)
        other = make_talk("KFPNUA")
        call_command("update_youtube_links", str(json_map), stdout=StringIO())
        mine.refresh_from_db()
        other.refresh_from_db()
        assert VIDEO_ID in mine.video_link
        assert other.video_link == ""
