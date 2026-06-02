"""
Unit tests for specific methods of the talks app models.

These tests focus on the _enrich_video_link, video_provider,
and video_link validation methods of the Talk model.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.models import Room, Streaming, Talk, prefetch_streamings
from talks.types import VideoProvider
from users.models import CustomUser


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
        """Reject video links from unknown providers; accept known providers and empty strings."""
        talk = baker.prepare(Talk, video_link=video_link)
        if expected_validation_error:
            with pytest.raises(ValidationError, match=r"URL must be from a valid video provider\."):
                talk.full_clean()
        else:
            # full_clean() must succeed without raising.
            talk.full_clean()


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


@pytest.mark.django_db
class TestAccessibleTo:
    """Tests for ``Talk.objects.accessible_to(user)``."""

    def test_superuser_sees_everything(self) -> None:
        """Superusers bypass the event filter entirely, regardless of event membership."""
        event_a = baker.make(Event, slug="a")
        event_b = baker.make(Event, slug="b")
        talk_a = baker.make(Talk, event=event_a)
        talk_b = baker.make(Talk, event=event_b)
        orphan = baker.make(Talk, event=None)

        su = baker.make(CustomUser, email="root@example.com", is_superuser=True)

        assert set(Talk.objects.accessible_to(su)) == {talk_a, talk_b, orphan}

    def test_regular_user_sees_only_their_events_and_orphans(self) -> None:
        """A regular user sees talks for their events plus talks with no event."""
        event_a = baker.make(Event, slug="a")
        event_b = baker.make(Event, slug="b")
        talk_a = baker.make(Talk, event=event_a)
        baker.make(Talk, event=event_b)  # not accessible
        orphan = baker.make(Talk, event=None)

        user = baker.make(CustomUser, email="u@example.com")
        user.events.add(event_a)

        assert set(Talk.objects.accessible_to(user)) == {talk_a, orphan}

    def test_user_without_any_events_still_sees_orphans(self) -> None:
        """A user with no event memberships only sees talks that have no event set."""
        event_a = baker.make(Event, slug="a")
        baker.make(Talk, event=event_a)  # not accessible
        orphan = baker.make(Talk, event=None)

        user = baker.make(CustomUser, email="newcomer@example.com")

        assert list(Talk.objects.accessible_to(user)) == [orphan]


@pytest.mark.django_db
class TestPrefetchStreamings:
    """Tests for the ``prefetch_streamings`` helper that batch-loads streamings."""

    def _make_talks(self, count: int, room: Room) -> list[Talk]:
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        return [
            baker.make(
                Talk,
                room=room,
                start_time=now + timedelta(minutes=30 * i),
                duration=timedelta(minutes=30),
                video_link="",
            )
            for i in range(count)
        ]

    def test_empty_list_is_noop(self) -> None:
        """Passing an empty list never touches the database."""
        with CaptureQueriesContext(connection) as ctx:
            prefetch_streamings([])
        assert len(ctx.captured_queries) == 0

    def test_matches_streaming_in_window(self) -> None:
        """A streaming covering the talk slot is cached on the talk."""
        room = baker.make(Room, name="Hall")
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        talk = baker.make(
            Talk,
            room=room,
            start_time=now,
            duration=timedelta(minutes=30),
            video_link="",
        )
        streaming = baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(minutes=5),
            end_time=now + timedelta(minutes=60),
        )

        # Re-fetch so no per-instance cache lingers from baker creation.
        talk = Talk.objects.get(pk=talk.pk)
        prefetch_streamings([talk])

        with CaptureQueriesContext(connection) as ctx:
            assert talk.get_streaming() == streaming
        assert len(ctx.captured_queries) == 0

    def test_no_match_caches_none(self) -> None:
        """Talks with no covering streaming get ``None`` cached, still skipping queries."""
        room = baker.make(Room, name="Hall")
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        talk = baker.make(
            Talk,
            room=room,
            start_time=now,
            duration=timedelta(minutes=30),
            video_link="",
        )
        # Streaming that ends before the talk starts - must not match.
        baker.make(
            Streaming,
            room=room,
            start_time=now - timedelta(hours=2),
            end_time=now - timedelta(hours=1),
        )

        talk = Talk.objects.get(pk=talk.pk)
        prefetch_streamings([talk])

        with CaptureQueriesContext(connection) as ctx:
            assert talk.get_streaming() is None
        assert len(ctx.captured_queries) == 0

    def test_avoids_n_plus_one(self) -> None:
        """A batch prefetch issues one streaming query regardless of talk count."""
        room = baker.make(Room, name="Hall")
        talks = self._make_talks(5, room)

        # Re-fetch all talks to drop any cached attributes from baker.
        talks = list(Talk.objects.filter(pk__in=[t.pk for t in talks]).select_related("room"))

        with CaptureQueriesContext(connection) as ctx:
            prefetch_streamings(talks)
            for t in talks:
                t.get_video_link()  # would normally trigger one streaming query per talk
        # One streaming SELECT, nothing else.
        assert len(ctx.captured_queries) == 1

    def test_room_without_streamings_still_caches_none(self) -> None:
        """Talks in rooms without any streaming are cached as ``None`` (no later query)."""
        room = baker.make(Room, name="Quiet Room")
        now = timezone.now()
        talk = baker.make(
            Talk,
            room=room,
            start_time=now,
            duration=timedelta(minutes=30),
            video_link="",
        )
        talk = Talk.objects.get(pk=talk.pk)
        prefetch_streamings([talk])
        with CaptureQueriesContext(connection) as ctx:
            assert talk.get_streaming() is None
        assert len(ctx.captured_queries) == 0
