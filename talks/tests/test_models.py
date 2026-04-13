"""
Unit tests for specific methods of the talks app models.

These tests focus on the _enrich_video_link, video_provider,
and video_link validation methods of the Talk model.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from model_bakery import baker

from talks.models import Room, Talk
from talks.types import VideoProvider


@pytest.mark.django_db
class TestTalkModel:
    """Test cases for specific Talk model methods."""

    @pytest.mark.parametrize(
        ("initial_link", "expected_link"),
        [
            (
                "https://youtube.com/watch?v=test",
                "https://youtube.com/watch?v=test&enablejsapi=1",
            ),
            ("https://youtube.com/watch", "https://youtube.com/watch?enablejsapi=1"),
            ("https://vimeo.com/test", "https://vimeo.com/test"),
            ("", ""),
        ],
    )
    def test_enrich_video_link(self, initial_link: str, expected_link: str) -> None:
        """Test the _enrich_video_link method ensures correct query parameters for YouTube."""
        start_time = datetime.now(tz=UTC) + timedelta(days=1)
        talk = baker.prepare(Talk, video_link=initial_link, start_time=start_time)

        with override_settings(SHOW_UPCOMING_TALKS_LINKS=True):
            talk.save()
            assert talk.video_link == expected_link

    @pytest.mark.parametrize(
        ("video_link", "expected_provider"),
        [
            # Full YouTube URLs and short youtu.be links both return "Youtube"
            # so the template only needs one string comparison.
            ("https://youtube.com/watch?v=test", VideoProvider.Youtube.name),
            ("https://youtu.be/test", VideoProvider.Youtube.name),
            ("https://vimeo.com/test", VideoProvider.Vimeo.name),
            ("", ""),
        ],
    )
    def test_video_provider(self, video_link: str, expected_provider: str) -> None:
        """Return the canonical provider name regardless of YouTube URL format."""
        talk = baker.prepare(Talk, video_link=video_link)
        talk.save()

        with override_settings(SHOW_UPCOMING_TALKS_LINKS=True):
            assert talk.video_provider == expected_provider

    @pytest.mark.parametrize(
        ("video_link", "expected_validation_error"),
        [
            ("http://invalid-video-link.com", True),
            ("https://vimeo.com/testvideo", False),
            ("https://youtube.com/testvideo", False),  # Valid YouTube link
            ("", False),  # Empty link should not raise a validation error
        ],
    )
    def test_video_link_validation(
        self,
        video_link: str,
        *,
        expected_validation_error: bool,
    ) -> None:
        """Test that video_link validation correctly handles valid and invalid providers."""
        talk = baker.prepare(Talk, video_link=video_link)
        if expected_validation_error:
            with pytest.raises(ValidationError, match=r"URL must be from a valid video provider\."):
                talk.full_clean()
        else:
            try:
                talk.full_clean()
            except ValidationError:
                pytest.fail("ValidationError raised unexpectedly")


@pytest.mark.django_db
class TestTalkRoomConflict:
    """Test has_room_conflict() and clean() prevent overlapping talks in the same room."""

    def test_no_conflict_empty_room(self) -> None:
        """No conflict when no talks exist in the room."""
        room = baker.make(Room)
        now = timezone.now()
        assert not Talk.has_room_conflict(room, now, timedelta(minutes=30))

    def test_no_conflict_sequential_talks(self) -> None:
        """Back-to-back talks do not conflict."""
        room = baker.make(Room)
        now = timezone.now()
        baker.make(Talk, room=room, start_time=now, duration=timedelta(minutes=30))
        assert not Talk.has_room_conflict(
            room,
            now + timedelta(minutes=30),
            timedelta(minutes=30),
        )

    def test_conflict_overlapping_talks(self) -> None:
        """A talk that starts before another ends is a conflict."""
        room = baker.make(Room)
        now = timezone.now()
        baker.make(Talk, room=room, start_time=now, duration=timedelta(minutes=45))
        assert Talk.has_room_conflict(
            room,
            now + timedelta(minutes=30),
            timedelta(minutes=30),
        )

    def test_no_conflict_different_rooms(self) -> None:
        """Overlapping times in different rooms are fine."""
        room_a = baker.make(Room, name="A")
        room_b = baker.make(Room, name="B")
        now = timezone.now()
        baker.make(Talk, room=room_a, start_time=now, duration=timedelta(minutes=45))
        assert not Talk.has_room_conflict(
            room_b,
            now,
            timedelta(minutes=45),
        )

    def test_exclude_pk_allows_self_update(self) -> None:
        """Updating an existing talk should not conflict with itself."""
        room = baker.make(Room)
        now = timezone.now()
        talk = baker.make(Talk, room=room, start_time=now, duration=timedelta(minutes=30))
        assert not Talk.has_room_conflict(
            room,
            now,
            timedelta(minutes=30),
            exclude_pk=talk.pk,
        )

    def test_returns_false_for_zero_duration(self) -> None:
        """Return False when duration is zero (falsy)."""
        room = baker.make(Room)
        now = timezone.now()
        assert not Talk.has_room_conflict(room, now, timedelta())

    def test_clean_skips_when_room_is_none(self) -> None:
        """clean() exits early without error when room is None."""
        talk = baker.prepare(Talk, room=None, duration=timedelta(minutes=30))
        talk.clean()  # Should not raise

    def test_clean_skips_when_duration_is_zero(self) -> None:
        """clean() exits early without error when duration is zero."""
        room = baker.make(Room)
        talk = baker.prepare(Talk, room=room, duration=timedelta())
        talk.clean()  # Should not raise

    def test_clean_raises_on_overlap(self) -> None:
        """Talk.clean() raises ValidationError when overlapping another talk."""
        room = baker.make(Room)
        now = timezone.now()
        baker.make(Talk, room=room, start_time=now, duration=timedelta(minutes=45))
        overlapping = baker.prepare(
            Talk,
            room=room,
            start_time=now + timedelta(minutes=30),
            duration=timedelta(minutes=30),
        )
        with pytest.raises(ValidationError, match="overlaps"):
            overlapping.clean()

    def test_clean_passes_for_non_overlapping(self) -> None:
        """Talk.clean() passes for non-overlapping talks."""
        room = baker.make(Room)
        now = timezone.now()
        baker.make(Talk, room=room, start_time=now, duration=timedelta(minutes=30))
        sequential = baker.prepare(
            Talk,
            room=room,
            start_time=now + timedelta(minutes=30),
            duration=timedelta(minutes=30),
        )
        sequential.clean()  # Should not raise

    def test_clean_passes_for_self_update(self) -> None:
        """Updating a saved talk should not conflict with itself."""
        room = baker.make(Room)
        now = timezone.now()
        talk = baker.make(Talk, room=room, start_time=now, duration=timedelta(minutes=30))
        talk.clean()  # Should not raise
