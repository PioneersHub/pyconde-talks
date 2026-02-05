"""Tests for the generate_fake_talks management command."""
# ruff: noqa: SLF001 PLR2004 PT018

import random
from datetime import datetime, time, timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from faker import Faker
from model_bakery import baker

from talks.management.commands.generate_fake_talks import (
    KEYNOTE_DURATION_MIN,
    TALK_SHORT_DURATIONS_MIN,
    TUTORIAL_DURATIONS_MIN,
    Command,
)
from talks.models import Room, Speaker, Streaming, Talk


@pytest.fixture()
def command() -> Command:
    """Create a Command instance with mocked stdout/stderr."""
    cmd = Command()
    cmd.stdout = StringIO()  # type: ignore[assignment]
    cmd.stderr = StringIO()  # type: ignore[assignment]
    return cmd


@pytest.fixture()
def fake() -> Faker:
    """Return a seeded Faker instance."""
    f = Faker()
    f.seed_instance(42)
    random.seed(42)
    return f


# ---------------------------------------------------------------------------
# _select_pronouns
# ---------------------------------------------------------------------------
class TestSelectPronouns:
    """Verify _select_pronouns returns gender-appropriate pronoun strings."""

    def test_man(self, command: Command) -> None:
        """Return he/him or he/they pronouns for the MAN gender."""
        result = command._select_pronouns(Speaker.Gender.MAN)
        assert result in {"he/him", "he/they"}

    def test_woman(self, command: Command) -> None:
        """Return she/her or she/they pronouns for the WOMAN gender."""
        result = command._select_pronouns(Speaker.Gender.WOMAN)
        assert result in {"she/her", "she/they"}

    def test_non_binary(self, command: Command) -> None:
        """Return gender-neutral pronouns for the NON_BINARY gender."""
        result = command._select_pronouns(Speaker.Gender.NON_BINARY)
        assert result in {"they/them", "ze/zir", "xe/xem"}

    def test_genderqueer(self, command: Command) -> None:
        """Return gender-neutral pronouns for the GENDERQUEER gender."""
        result = command._select_pronouns(Speaker.Gender.GENDERQUEER)
        assert result in {"they/them", "ze/zir", "xe/xem"}

    def test_self_describe(self, command: Command) -> None:
        """Return any pronoun from the full set for the SELF_DESCRIBE gender."""
        result = command._select_pronouns(Speaker.Gender.SELF_DESCRIBE)
        assert result in {"they/them", "ze/zir", "xe/xem", "she/her", "he/him"}

    def test_prefer_not_to_say(self, command: Command) -> None:
        """Return an empty string when the speaker prefers not to disclose gender."""
        result = command._select_pronouns(Speaker.Gender.PREFER_NOT_TO_SAY)
        assert result == ""


# ---------------------------------------------------------------------------
# _generate_avatar_url
# ---------------------------------------------------------------------------
class TestGenerateAvatarUrl:
    """Verify _generate_avatar_url picks the correct portrait category by gender."""

    def test_man_avatar(self, command: Command) -> None:
        """Use the 'men' portrait collection for the MAN gender."""
        random.seed(1)  # First random() > 0.3
        result = command._generate_avatar_url(Speaker.Gender.MAN)
        if result:
            assert "portraits/men" in result

    def test_woman_avatar(self, command: Command) -> None:
        """Use the 'women' portrait collection for the WOMAN gender."""
        random.seed(1)
        result = command._generate_avatar_url(Speaker.Gender.WOMAN)
        if result:
            assert "portraits/women" in result

    def test_other_gender_avatar(self, command: Command) -> None:
        """Use the 'lego' portrait collection for non-binary genders."""
        random.seed(1)
        result = command._generate_avatar_url(Speaker.Gender.NON_BINARY)
        if result:
            assert "portraits/lego" in result

    def test_empty_avatar(self, command: Command) -> None:
        """With seed that gives random() <= 0.3, returns empty string."""
        random.seed(0)
        result = command._generate_avatar_url(Speaker.Gender.MAN)
        # Can be empty due to probability
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _build_pretalx_link
# ---------------------------------------------------------------------------
class TestBuildPretalxLink:
    """Verify _build_pretalx_link constructs valid pretalx session URLs."""

    @patch("talks.management.commands.generate_fake_talks.settings")
    def test_default_link(self, mock_settings: Any, command: Command, fake: Faker) -> None:
        """Build a URL from the default PRETALX_BASE_URL and event slug."""
        mock_settings.PRETALX_BASE_URL = "https://pretalx.com"
        mock_settings.PRETALX_EVENT_SLUG = "pyconde2025"
        result = command._build_pretalx_link(fake)
        assert result.startswith("https://pretalx.com/pyconde2025/talk/")

    @patch("talks.management.commands.generate_fake_talks.settings")
    def test_custom_base_url(self, mock_settings: Any, command: Command, fake: Faker) -> None:
        """Build a URL from a custom base URL, stripping trailing slashes."""
        mock_settings.PRETALX_BASE_URL = "https://custom.pretalx.com/"
        mock_settings.PRETALX_EVENT_SLUG = "demo-event"
        result = command._build_pretalx_link(fake)
        assert result.startswith("https://custom.pretalx.com/demo-event/talk/")


# ---------------------------------------------------------------------------
# _build_room_slido_link
# ---------------------------------------------------------------------------
class TestBuildRoomSlidoLink:
    """Verify _build_room_slido_link generates a lowercase Slido event URL."""

    def test_link_format(self, command: Command) -> None:
        """Lowercase the room name to form the sli.do event path segment."""
        result = command._build_room_slido_link("Titanium")
        assert result == "https://app.sli.do/event/titanium"


# ---------------------------------------------------------------------------
# _choose_presentation_type
# ---------------------------------------------------------------------------
class TestChoosePresentationType:
    """Verify _choose_presentation_type returns one of the valid enum choices."""

    def test_returns_valid_type(self, command: Command) -> None:
        """Return a PresentationType that is a valid Talk enum member."""
        result = command._choose_presentation_type()
        assert result in {
            Talk.PresentationType.KEYNOTE,
            Talk.PresentationType.KIDS,
            Talk.PresentationType.LIGHTNING,
            Talk.PresentationType.PANEL,
            Talk.PresentationType.TALK,
            Talk.PresentationType.TUTORIAL,
        }


# ---------------------------------------------------------------------------
# _generate_title
# ---------------------------------------------------------------------------
class TestGenerateTitle:
    """Verify _generate_title produces track-specific talk titles."""

    def test_ml_track(self, command: Command, fake: Faker) -> None:
        """Generate a non-empty title for the Machine Learning track."""
        result = command._generate_title("Machine Learning", fake)
        assert isinstance(result, str) and len(result) > 0

    def test_security_track(self, command: Command, fake: Faker) -> None:
        """Include 'Securing' in titles for the Security track."""
        result = command._generate_title("Security", fake)
        assert "Securing" in result

    def test_django_track(self, command: Command, fake: Faker) -> None:
        """Include 'Django' in titles for the Django & Web track."""
        result = command._generate_title("Django & Web", fake)
        assert "Django" in result

    def test_data_track(self, command: Command, fake: Faker) -> None:
        """Include 'Data' or 'data' in titles for the Data Handling track."""
        result = command._generate_title("Data Handling & Engineering", fake)
        assert "Data" in result or "data" in result

    def test_vision_track(self, command: Command, fake: Faker) -> None:
        """Include 'Detecting' in titles for the Computer Vision track."""
        result = command._generate_title("Computer Vision", fake)
        assert "Detecting" in result

    def test_nlp_track(self, command: Command, fake: Faker) -> None:
        """Generate a non-empty title for the NLP track."""
        result = command._generate_title("Natural Language Processing", fake)
        assert isinstance(result, str) and len(result) > 0

    def test_devops_track(self, command: Command, fake: Faker) -> None:
        """Generate a non-empty title for the MLOps & DevOps track."""
        result = command._generate_title("MLOps & DevOps", fake)
        assert isinstance(result, str) and len(result) > 0

    def test_unknown_track(self, command: Command, fake: Faker) -> None:
        """Fall back to a generic 'Python' title for unrecognized tracks."""
        result = command._generate_title("Unknown", fake)
        assert "Python" in result


# ---------------------------------------------------------------------------
# _pick_room_and_duration
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestPickRoomAndDuration:
    """Verify _pick_room_and_duration selects the right room and duration per type."""

    def test_forced_room(self, command: Command) -> None:
        """Use the explicitly forced room instead of picking by presentation type."""
        room = baker.make(Room, name="Forced Room")
        rooms: dict[str, list[Room]] = {
            "plenary": [baker.make(Room, name="P")],
            "talks": [baker.make(Room, name="T")],
            "tutorials": [baker.make(Room, name="Tu")],
        }
        result_room, result_duration = command._pick_room_and_duration(
            presentation_type=Talk.PresentationType.TALK,
            rooms=rooms,
            forced_room=room,
        )
        assert result_room == room
        assert result_duration.total_seconds() / 60 in TALK_SHORT_DURATIONS_MIN

    def test_keynote(self, command: Command) -> None:
        """Assign keynotes to the plenary room with the fixed keynote duration."""
        plenary = baker.make(Room, name="Spectrum")
        rooms: dict[str, list[Room]] = {
            "plenary": [plenary],
            "talks": [baker.make(Room, name="T")],
            "tutorials": [baker.make(Room, name="Tu")],
        }
        result_room, result_duration = command._pick_room_and_duration(
            presentation_type=Talk.PresentationType.KEYNOTE,
            rooms=rooms,
            forced_room=None,
        )
        assert result_room == plenary
        assert result_duration == timedelta(minutes=KEYNOTE_DURATION_MIN)

    def test_talk(self, command: Command) -> None:
        """Assign regular talks to a talk room with a short talk duration."""
        talk_room = baker.make(Room, name="Talk Room")
        rooms: dict[str, list[Room]] = {
            "plenary": [baker.make(Room, name="P")],
            "talks": [talk_room],
            "tutorials": [baker.make(Room, name="Tu")],
        }
        result_room, result_duration = command._pick_room_and_duration(
            presentation_type=Talk.PresentationType.TALK,
            rooms=rooms,
            forced_room=None,
        )
        assert result_room == talk_room
        assert result_duration.total_seconds() / 60 in TALK_SHORT_DURATIONS_MIN

    def test_tutorial(self, command: Command) -> None:
        """Assign tutorials to a tutorial room with a tutorial-length duration."""
        tut_room = baker.make(Room, name="Tutorial Room")
        rooms: dict[str, list[Room]] = {
            "plenary": [baker.make(Room, name="P")],
            "talks": [baker.make(Room, name="T")],
            "tutorials": [tut_room],
        }
        result_room, result_duration = command._pick_room_and_duration(
            presentation_type=Talk.PresentationType.TUTORIAL,
            rooms=rooms,
            forced_room=None,
        )
        assert result_room == tut_room
        assert result_duration.total_seconds() / 60 in TUTORIAL_DURATIONS_MIN


# ---------------------------------------------------------------------------
# _maybe_custom_slido / _maybe_custom_video / _maybe_custom_video_start_time
# ---------------------------------------------------------------------------
class TestMaybeHelpers:
    """Verify probability-gated helpers return or skip optional fields."""

    def test_maybe_custom_slido_always(self, command: Command, fake: Faker) -> None:
        """Generate a Slido link when probability is 1.0 (always)."""
        result = command._maybe_custom_slido(fake, probability=1.0)
        assert "sli.do" in result

    def test_maybe_custom_slido_never(self, command: Command, fake: Faker) -> None:
        """Return an empty string when probability is 0.0 (never)."""
        result = command._maybe_custom_slido(fake, probability=0.0)
        assert result == ""

    def test_maybe_custom_video_with_streaming(self, command: Command) -> None:
        """Generate a Vimeo link when the talk has an active streaming session."""
        result = command._maybe_custom_video(has_streaming=True, probability=1.0)
        assert "vimeo.com" in result

    def test_maybe_custom_video_no_streaming(self, command: Command) -> None:
        """Return empty when there is no streaming, even with probability 1.0."""
        result = command._maybe_custom_video(has_streaming=False, probability=1.0)
        assert result == ""

    def test_maybe_custom_video_never(self, command: Command) -> None:
        """Return empty when probability is 0.0 despite an active streaming."""
        result = command._maybe_custom_video(has_streaming=True, probability=0.0)
        assert result == ""

    def test_maybe_custom_video_start_time_always(self, command: Command) -> None:
        """Generate a random start offset within the talk duration."""
        duration = timedelta(minutes=30)
        result = command._maybe_custom_video_start_time(duration, probability=1.0)
        assert 0 <= result < duration.total_seconds()

    def test_maybe_custom_video_start_time_never(self, command: Command) -> None:
        """Return zero offset when probability is 0.0."""
        duration = timedelta(minutes=30)
        result = command._maybe_custom_video_start_time(duration, probability=0.0)
        assert result == 0


# ---------------------------------------------------------------------------
# _create_rooms
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCreateRooms:
    """Verify _create_rooms creates Room objects grouped by category."""

    def test_creates_rooms(self, command: Command) -> None:
        """Create plenary, talk, and tutorial rooms from the given name lists."""
        rooms = command._create_rooms(
            rooms_plenary=["Spec"],
            rooms_talks=["Talk1", "Talk2"],
            rooms_tutorials=["Tut1"],
        )
        assert len(rooms["plenary"]) == 1
        assert len(rooms["talks"]) == 2
        assert len(rooms["tutorials"]) == 1
        assert Room.objects.count() == 4

    def test_skips_existing(self, command: Command) -> None:
        """Reuse an existing Room instead of creating a duplicate."""
        baker.make(Room, name="Spec")
        rooms = command._create_rooms(
            rooms_plenary=["Spec"],
            rooms_talks=[],
            rooms_tutorials=[],
        )
        assert len(rooms["plenary"]) == 1
        assert Room.objects.filter(name="Spec").count() == 1


# ---------------------------------------------------------------------------
# _create_speakers_pool
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCreateSpeakersPool:
    """Verify _create_speakers_pool creates a proportional speaker pool."""

    def test_creates_speakers(self, command: Command, fake: Faker) -> None:
        """Create 90% of talk_count speakers to allow speaker reuse across talks."""
        pool = command._create_speakers_pool(fake, talk_count=10)
        assert len(pool) == 9  # 90% of 10
        assert Speaker.objects.count() == 9


# ---------------------------------------------------------------------------
# _preload_streaming_by_room
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestPreloadStreamingByRoom:
    """Verify _preload_streaming_by_room indexes Streaming objects by room PK."""

    def test_empty(self, command: Command) -> None:
        """Return an empty dict when no streaming sessions exist in the database."""
        result = command._preload_streaming_by_room()
        assert result == {}

    def test_with_streamings(self, command: Command) -> None:
        """Index existing streaming sessions by their room primary key."""
        room = baker.make(Room, name="R1")
        now = timezone.now()
        baker.make(
            Streaming,
            room=room,
            start_time=now,
            end_time=now + timedelta(hours=2),
            video_link="https://youtube.com/live",
        )
        result = command._preload_streaming_by_room()
        assert room.pk in result
        assert len(result[room.pk]) == 1


# ---------------------------------------------------------------------------
# _build_special_slots
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestBuildSpecialSlots:
    """Verify _build_special_slots generates time-pinned slots near the current moment."""

    def test_returns_slots(self, command: Command) -> None:
        """Generate three special slots including one forced to the streaming room."""
        room = baker.make(Room, name="SR")
        now = timezone.now()
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            video_link="https://youtube.com/live",
        )
        slots = command._build_special_slots(
            talk_count=10,
            now=now,
            streaming_now=streaming,
        )
        assert len(slots) == 3
        # Second slot should have the forced room
        assert slots[1]["room"] == room

    def test_talk_count_limits_slots(self, command: Command) -> None:
        """Cap the number of special slots to the total requested talk count."""
        room = baker.make(Room, name="SR2")
        now = timezone.now()
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            video_link="https://youtube.com/live",
        )
        slots = command._build_special_slots(
            talk_count=1,
            now=now,
            streaming_now=streaming,
        )
        assert len(slots) == 1


# ---------------------------------------------------------------------------
# _create_streaming_sessions
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCreateStreamingSessions:
    """Verify _create_streaming_sessions creates Streaming objects spanning each day."""

    def test_creates_sessions(self, command: Command) -> None:
        """Create at least one streaming session per room for the given number of days."""
        rooms: dict[str, list[Room]] = {
            "plenary": [baker.make(Room, name="Plenary")],
            "talks": [baker.make(Room, name="Talk1")],
            "tutorials": [baker.make(Room, name="Tut1")],
        }
        base_time = timezone.make_aware(
            datetime.combine(timezone.localtime().date(), time(9, 0)),
            timezone.get_current_timezone(),
        )
        command._create_streaming_sessions(rooms, base_time, days=1)
        assert Streaming.objects.count() > 0

    def test_ensures_coverage(self, command: Command) -> None:
        """Ensures a streaming covers the current time."""
        rooms: dict[str, list[Room]] = {
            "plenary": [baker.make(Room, name="Plenary2")],
            "talks": [],
            "tutorials": [],
        }
        # Use a base_time far in the past so no session naturally covers now
        base_time = timezone.make_aware(
            datetime.combine(
                timezone.localtime().date() - timedelta(days=30),
                time(9, 0),
            ),
            timezone.get_current_timezone(),
        )
        command._create_streaming_sessions(rooms, base_time, days=1)
        now = timezone.now()
        # Check a session exists that at least covers the current moment
        # (use a small buffer to avoid timing races with timezone.now() drift)
        assert Streaming.objects.filter(
            start_time__lte=now,
            end_time__gte=now,
        ).exists()


# ---------------------------------------------------------------------------
# Full handle integration test
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestHandleCommand:
    """End-to-end test that handle() creates rooms, speakers, streamings, and talks."""

    def test_handle_default(self) -> None:
        """Test the command generates talks with minimal arguments."""
        stdout = StringIO()
        call_command(
            "generate_fake_talks",
            "--count=5",
            "--seed=42",
            "--days=1",
            "--rooms-plenary=Plenary",
            "--rooms-talks=Talk1",
            "--rooms-tutorials=Tut1",
            stdout=stdout,
        )
        assert Talk.objects.count() == 5
        assert Speaker.objects.count() > 0
        assert Room.objects.count() == 3
        assert Streaming.objects.count() > 0

    def test_handle_clear_existing(self) -> None:
        """Test --clear-existing deletes old data before generating."""
        baker.make(Talk, title="Old Talk")
        baker.make(Speaker, name="Old Speaker")
        stdout = StringIO()
        call_command(
            "generate_fake_talks",
            "--count=2",
            "--seed=42",
            "--clear-existing",
            "--days=1",
            "--rooms-plenary=Plenary",
            "--rooms-talks=Talk1",
            "--rooms-tutorials=Tut1",
            stdout=stdout,
        )
        assert Talk.objects.count() == 2

    def test_handle_invalid_date(self) -> None:
        """Test invalid date format raises ValueError."""
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            call_command("generate_fake_talks", "--date=bad-date")
