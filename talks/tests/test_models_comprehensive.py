"""Comprehensive tests for talks.models covering all uncovered branches."""
# ruff: noqa: PLR2004

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from model_bakery import baker

from talks.models import EMPTY_TRACK_NAME, Room, Speaker, Streaming, Talk


# ---------------------------------------------------------------------------
# Room
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRoom:
    """Tests for the Room model."""

    def test_str(self) -> None:
        """Use the room name as the string representation."""
        room = baker.make(Room, name="Main Hall")
        assert str(room) == "Main Hall"

    def test_is_streaming_live_true(self) -> None:
        """Return True when a streaming session is currently active in this room."""
        room = baker.make(Room)
        now = timezone.now()
        baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            video_link="https://youtube.com/live",
        )
        assert room.is_streaming_live() is True

    def test_is_streaming_live_false(self) -> None:
        """Return False when no streaming session is currently active."""
        room = baker.make(Room)
        assert room.is_streaming_live() is False


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestStreaming:
    """Tests for the Streaming model."""

    def test_str(self) -> None:
        """Include room name in the string representation."""
        room = baker.make(Room, name="Room A")
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=datetime(2025, 6, 1, 9, 0, tzinfo=UTC),
            end_time=datetime(2025, 6, 1, 17, 0, tzinfo=UTC),
            video_link="https://youtube.com/live",
        )
        result = str(streaming)
        assert "Room A" in result
        assert "Streaming for" in result

    def test_clean_no_overlap(self) -> None:
        """Allow non-overlapping streamings and self-validation on the same room."""
        room = baker.make(Room)
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=datetime(2025, 6, 1, 9, 0, tzinfo=UTC),
            end_time=datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
            video_link="https://youtube.com/live",
        )
        # Non-overlapping streaming should be fine
        s2 = baker.prepare(
            Streaming,
            room=room,
            start_time=datetime(2025, 6, 1, 13, 0, tzinfo=UTC),
            end_time=datetime(2025, 6, 1, 15, 0, tzinfo=UTC),
            video_link="https://youtube.com/live2",
        )
        s2.clean()  # Should not raise

        # Self-validation should pass (updating existing)
        streaming.clean()  # Should not raise

    def test_clean_overlap_raises(self) -> None:
        """Raise ValidationError when a new streaming overlaps an existing one."""
        room = baker.make(Room)
        baker.make(
            Streaming,
            room=room,
            start_time=datetime(2025, 6, 1, 9, 0, tzinfo=UTC),
            end_time=datetime(2025, 6, 1, 12, 0, tzinfo=UTC),
            video_link="https://youtube.com/live",
        )
        s2 = baker.prepare(
            Streaming,
            room=room,
            start_time=datetime(2025, 6, 1, 11, 0, tzinfo=UTC),
            end_time=datetime(2025, 6, 1, 15, 0, tzinfo=UTC),
            video_link="https://youtube.com/live2",
        )
        with pytest.raises(ValidationError, match="overlaps"):
            s2.clean()

    def test_is_active_true(self) -> None:
        """Return True when the current time falls within the streaming window."""
        room = baker.make(Room)
        now = timezone.now()
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=1),
            video_link="https://youtube.com/live",
        )
        assert streaming.is_active() is True

    def test_is_active_false(self) -> None:
        """Return False when the streaming window has already ended."""
        room = baker.make(Room)
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=timezone.now() - timedelta(hours=3),
            end_time=timezone.now() - timedelta(hours=1),
            video_link="https://youtube.com/live",
        )
        assert streaming.is_active() is False


# ---------------------------------------------------------------------------
# Speaker
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSpeaker:
    """Tests for the Speaker model."""

    def test_str(self) -> None:
        """Use the speaker name as the string representation."""
        speaker = baker.make(Speaker, name="Jane Doe")
        assert str(speaker) == "Jane Doe"


# ---------------------------------------------------------------------------
# Talk
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTalkComprehensive:
    """Comprehensive tests for the Talk model."""

    def test_str(self) -> None:
        """Include the title and speaker names in the string representation."""
        talk = baker.make(Talk, title="My Talk")
        speaker = baker.make(Speaker, name="Alice")
        talk.speakers.add(speaker)
        assert str(talk) == "My Talk by Alice"

    def test_save_sets_default_duration(self) -> None:
        """Auto-set a default 45-minute duration for keynotes when saved with zero."""
        talk = baker.make(
            Talk,
            presentation_type=Talk.PresentationType.KEYNOTE,
            duration=timedelta(),
        )
        assert talk.duration == timedelta(minutes=45)

    def test_save_sets_lightning_track(self) -> None:
        """Auto-set the track to 'Lightning Talks' for lightning presentations."""
        talk = baker.make(
            Talk,
            presentation_type=Talk.PresentationType.LIGHTNING,
            track="",
        )
        assert talk.track == "Lightning Talks"

    def test_save_sets_default_track(self) -> None:
        """Assign the EMPTY_TRACK_NAME fallback when no track is specified."""
        talk = baker.make(Talk, presentation_type=Talk.PresentationType.TALK, track="")
        assert talk.track == EMPTY_TRACK_NAME

    def test_save_keeps_existing_track(self) -> None:
        """Preserve the existing track name when one is already set."""
        talk = baker.make(Talk, track="PyData")
        assert talk.track == "PyData"

    # --- get_streaming ---
    def test_get_streaming_no_room(self) -> None:
        """Return None when the talk has no room assigned."""
        talk = baker.make(Talk, room=None)
        assert talk.get_streaming() is None

    def test_get_streaming_with_matching_streaming(self) -> None:
        """Return the streaming that covers the talk's start time."""
        room = baker.make(Room)
        now = timezone.now()
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            video_link="https://youtube.com/live",
        )
        talk = baker.make(
            Talk,
            room=room,
            start_time=now,
            duration=timedelta(minutes=30),
        )
        assert talk.get_streaming() == streaming

    def test_get_streaming_no_matching(self) -> None:
        """Return None when no streaming covers the talk's time slot."""
        room = baker.make(Room)
        tomorrow = timezone.now() + timedelta(days=1)
        baker.make(
            Streaming,
            room=room,
            start_time=timezone.now() - timedelta(hours=5),
            end_time=timezone.now() - timedelta(hours=3),
            video_link="https://youtube.com/live",
        )
        talk = baker.make(Talk, room=room, start_time=tomorrow, duration=timedelta(minutes=30))
        assert talk.get_streaming() is None

    # --- get_video_start_time ---
    def test_get_video_start_time_explicit(self) -> None:
        """Return the explicit video_start_time when set on the talk."""
        talk = baker.make(Talk, video_start_time=120)
        assert talk.get_video_start_time() == 120

    def test_get_video_start_time_from_streaming(self) -> None:
        """Calculate offset from streaming start when no explicit start time is set."""
        room = baker.make(Room)
        now = timezone.now()
        baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            video_link="https://youtube.com/live",
        )
        talk = baker.make(
            Talk,
            room=room,
            start_time=now,
            duration=timedelta(minutes=30),
            video_start_time=None,
        )
        result = talk.get_video_start_time()
        assert result == pytest.approx(3600, abs=5)

    def test_get_video_start_time_no_streaming(self) -> None:
        """Fall back to zero when no streaming exists and no explicit value is set."""
        talk = baker.make(Talk, video_start_time=None, room=None)
        assert talk.get_video_start_time() == 0

    # --- get_video_link ---
    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_get_video_link_own_link(self) -> None:
        """Return the talk's own video link with the YouTube JS API parameter."""
        talk = baker.make(Talk, video_link="https://youtube.com/watch?v=abc")
        assert talk.get_video_link() == "https://youtube.com/watch?v=abc&enablejsapi=1"

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=False)
    def test_get_video_link_upcoming_hidden(self) -> None:
        """Return empty when the talk is upcoming and SHOW_UPCOMING_TALKS_LINKS is off."""
        future = timezone.now() + timedelta(days=30)
        talk = baker.make(
            Talk,
            start_time=future,
            duration=timedelta(minutes=30),
            video_link="",
        )
        assert talk.get_video_link() == ""

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_get_video_link_from_streaming(self) -> None:
        """Fall back to the streaming's video link when the talk has no own link."""
        room = baker.make(Room)
        now = timezone.now()
        # Create talk FIRST (before streaming exists) so _enrich_video_link
        # during save() doesn't pick up the streaming's YouTube link.
        talk = baker.make(
            Talk,
            room=room,
            start_time=now,
            duration=timedelta(minutes=30),
            video_link="",
        )
        baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            video_link="https://youtube.com/live",
        )
        assert talk.get_video_link() == "https://youtube.com/live"

    def test_get_video_link_no_room_no_link(self) -> None:
        """Return empty when the talk has no room and no video link."""
        talk = baker.make(
            Talk,
            room=None,
            video_link="",
            start_time=timezone.now() - timedelta(hours=2),
            duration=timedelta(minutes=30),
        )
        assert talk.get_video_link() == ""

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_get_video_link_room_no_streaming(self) -> None:
        """Return empty when the room exists but has no active streaming."""
        room = baker.make(Room)
        talk = baker.make(
            Talk,
            room=room,
            video_link="",
            start_time=timezone.now() - timedelta(hours=2),
            duration=timedelta(minutes=30),
        )
        assert talk.get_video_link() == ""

    # --- video_provider ---
    def test_video_provider_empty(self) -> None:
        """Return an empty string when no video link is set."""
        talk = baker.make(Talk, video_link="", room=None)
        assert talk.video_provider == ""

    # --- speaker_names ---
    def test_speaker_names_zero(self) -> None:
        """Return an empty string when the talk has no speakers."""
        talk = baker.make(Talk)
        assert talk.speaker_names == ""

    def test_speaker_names_one(self) -> None:
        """Return the single speaker name without any separator."""
        talk = baker.make(Talk)
        talk.speakers.add(baker.make(Speaker, name="Alice"))
        assert talk.speaker_names == "Alice"

    def test_speaker_names_two(self) -> None:
        """Join two speaker names with an ampersand."""
        talk = baker.make(Talk)
        talk.speakers.add(baker.make(Speaker, name="Alice"))
        talk.speakers.add(baker.make(Speaker, name="Bob"))
        assert "&" in talk.speaker_names

    def test_speaker_names_three(self) -> None:
        """Join three speaker names with commas and a final ampersand."""
        talk = baker.make(Talk)
        for name in ("Alice", "Bob", "Charlie"):
            talk.speakers.add(baker.make(Speaker, name=name))
        result = talk.speaker_names
        assert "," in result
        assert "&" in result

    def test_speaker_names_four_or_more(self) -> None:
        """Truncate to the first speakers and append 'more' for four or more."""
        talk = baker.make(Talk)
        for name in ("Alice", "Bob", "Charlie", "Diana"):
            talk.speakers.add(baker.make(Speaker, name=name))
        assert "more" in talk.speaker_names

    # --- get_timing ---
    def test_get_timing_past(self) -> None:
        """Return PAST when the talk has already ended."""
        talk = baker.make(
            Talk,
            start_time=timezone.now() - timedelta(hours=3),
            duration=timedelta(minutes=30),
        )
        assert talk.get_timing() == Talk.TalkTiming.PAST

    def test_get_timing_current(self) -> None:
        """Return CURRENT when the talk is happening right now."""
        talk = baker.make(
            Talk,
            start_time=timezone.now() - timedelta(minutes=10),
            duration=timedelta(minutes=30),
        )
        assert talk.get_timing() == Talk.TalkTiming.CURRENT

    def test_get_timing_upcoming(self) -> None:
        """Return UPCOMING when the talk has not started yet."""
        talk = baker.make(
            Talk,
            start_time=timezone.now() + timedelta(hours=3),
            duration=timedelta(minutes=30),
        )
        assert talk.get_timing() == Talk.TalkTiming.UPCOMING

    # --- is_upcoming / is_current ---
    def test_is_upcoming(self) -> None:
        """Return True for is_upcoming and False for is_current on a future talk."""
        talk = baker.make(
            Talk,
            start_time=timezone.now() + timedelta(hours=3),
            duration=timedelta(minutes=30),
        )
        assert talk.is_upcoming() is True
        assert talk.is_current() is False

    def test_is_current(self) -> None:
        """Return True for is_current when the talk started recently."""
        talk = baker.make(
            Talk,
            start_time=timezone.now() - timedelta(minutes=5),
            duration=timedelta(minutes=30),
        )
        assert talk.is_current() is True

    # --- has_active_streaming ---
    def test_has_active_streaming_true(self) -> None:
        """Return True when the room has a currently active streaming session."""
        room = baker.make(Room)
        now = timezone.now()
        baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            video_link="https://youtube.com/live",
        )
        talk = baker.make(
            Talk,
            room=room,
            start_time=now,
            duration=timedelta(minutes=30),
        )
        assert talk.has_active_streaming() is True

    def test_has_active_streaming_false(self) -> None:
        """Return False when the talk has no room assigned."""
        talk = baker.make(Talk, room=None)
        assert talk.has_active_streaming() is False

    # --- get_image_url ---
    @override_settings(MEDIA_URL="/media/", BRAND_ASSETS_SUBDIR="test")
    def test_get_image_url_with_image(self) -> None:
        """Return the uploaded image URL from MEDIA_URL."""
        talk = baker.make(Talk, image="talk_images/test/img.jpg")
        assert "img.jpg" in talk.get_image_url()

    @override_settings(MEDIA_URL="/media/", BRAND_ASSETS_SUBDIR="test")
    def test_get_image_url_with_external_url(self) -> None:
        """Prefer the external image URL when no uploaded image is set."""
        talk = baker.make(Talk, external_image_url="https://example.com/img.jpg")
        assert talk.get_image_url() == "https://example.com/img.jpg"

    @override_settings(MEDIA_URL="/media/", BRAND_ASSETS_SUBDIR="test")
    def test_get_image_url_default(self) -> None:
        """Fall back to the branded default image when no image source exists."""
        talk = baker.make(Talk, image="", external_image_url="")
        result = talk.get_image_url()
        assert "default.jpg" in result
        assert "test" in result

    # --- get_slido_link ---
    def test_get_slido_link_own(self) -> None:
        """Return the talk's own Slido link when set."""
        talk = baker.make(Talk, slido_link="https://slido.com/123")
        assert talk.get_slido_link() == "https://slido.com/123"

    def test_get_slido_link_from_room(self) -> None:
        """Fall back to the room's Slido link when the talk has none."""
        room = baker.make(Room, slido_link="https://slido.com/room")
        talk = baker.make(Talk, room=room, slido_link="")
        assert talk.get_slido_link() == "https://slido.com/room"

    def test_get_slido_link_empty(self) -> None:
        """Return empty when neither the talk nor its room has a Slido link."""
        room = baker.make(Room, slido_link="")
        talk = baker.make(Talk, room=room, slido_link="")
        assert talk.get_slido_link() == ""

    def test_get_slido_link_no_room(self) -> None:
        """Return empty when the talk has no room assigned and no own Slido link."""
        talk = baker.make(Talk, room=None, slido_link="")
        assert talk.get_slido_link() == ""
