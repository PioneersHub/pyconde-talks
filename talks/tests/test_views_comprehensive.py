"""Tests for talks.views."""
# ruff: noqa: PLR2004

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.core.cache import cache
from django.db import IntegrityError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.models import Rating, Room, Speaker, Talk
from users.models import CustomUser
from utils.test_perf import assert_no_n_plus_one


if TYPE_CHECKING:
    from collections.abc import Generator

    from django.test.client import Client


@pytest.fixture()
def event() -> Event:
    """Return a shared event most view tests put their talks in (talks are event-scoped)."""
    return Event.objects.create(slug="view", name="View", year=2099)


@pytest.fixture()
def user(event: Event) -> CustomUser:
    """Create a regular user with access to the shared event."""
    user = baker.make(CustomUser, email="viewer@example.com")
    user.events.add(event)
    return user


@pytest.mark.django_db
class TestTalkDetailView:
    """Tests for TalkDetailView."""

    def test_authenticated_user_can_view_detail(
        self, client: Client, user: CustomUser, event: Event
    ) -> None:
        """Allow authenticated users to view a talk's detail page."""
        talk = baker.make(Talk, event=event, title="Detail Talk")
        client.force_login(user)
        url = reverse("talk_detail", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Detail Talk" in response.content.decode()

    def test_unauthenticated_user_redirected(self, client: Client, event: Event) -> None:
        """Redirect unauthenticated users to the login page."""
        talk = baker.make(Talk, event=event)
        url = reverse("talk_detail", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND

    def test_nonexistent_talk_404(self, client: Client, user: CustomUser) -> None:
        """Return 404 when the requested talk PK does not exist."""
        client.force_login(user)
        url = reverse("talk_detail", args=[99999])
        response = client.get(url)
        assert response.status_code == HTTPStatus.NOT_FOUND

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_vimeo_player_script_included_for_vimeo_talk(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """Render the Vimeo Player API script when the talk has a Vimeo video link."""
        past = timezone.now() - timedelta(hours=2)
        talk = baker.make(
            Talk,
            event=event,
            video_link="https://vimeo.com/123456789",
            video_start_time=120,
            start_time=past,
            duration=timedelta(minutes=30),
        )
        client.force_login(user)
        response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        # The Vimeo Player API script must be present so the click handler can be registered.
        assert "player.vimeo.com/api/player.js" in content
        assert "Vimeo.Player" in content
        assert "jump-to-time" in content

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_vimeo_player_script_not_included_without_start_time(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """Skip the jump-to-time button when the talk has no video_start_time."""
        past = timezone.now() - timedelta(hours=2)
        talk = baker.make(
            Talk,
            event=event,
            video_link="https://vimeo.com/123456789",
            video_start_time=None,
            start_time=past,
            duration=timedelta(minutes=30),
            room=None,
        )
        client.force_login(user)
        response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        # Player API is still loaded (needed for future Vimeo interactions)
        # but the jump button should not be rendered.
        assert "jump-to-time" not in content

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_youtube_player_script_included_for_youtube_talk(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """Render the YouTube IFrame API script when the talk has a YouTube video link."""
        past = timezone.now() - timedelta(hours=2)
        talk = baker.make(
            Talk,
            event=event,
            video_link="https://youtube.com/embed/abc123",
            video_start_time=60,
            start_time=past,
            duration=timedelta(minutes=30),
        )
        client.force_login(user)
        response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "youtube.com/iframe_api" in content
        assert "seekTo" in content

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_talk_detail_no_n_plus_one(self, client: Client, user: CustomUser) -> None:
        """The detail page calls streaming-dependent methods many times; one query each tops."""
        event = baker.make(Event, is_active=True)
        user.events.add(event)
        room = baker.make(Room)
        talk = baker.make(
            Talk,
            event=event,
            room=room,
            start_time=timezone.now() - timedelta(hours=1),
            duration=timedelta(minutes=30),
            video_link="",
        )
        for i in range(3):
            speaker = baker.make(Speaker, name=f"Speaker {i}")
            talk.speakers.add(speaker)

        client.force_login(user)
        with assert_no_n_plus_one():
            response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK

    def test_youtube_short_url_loads_youtube_player(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """Treat youtu.be links as YouTube so the IFrame API is loaded."""
        past = timezone.now() - timedelta(hours=2)
        talk = baker.make(
            Talk,
            event=event,
            video_link="https://youtu.be/abc123",
            video_start_time=60,
            start_time=past,
            duration=timedelta(minutes=30),
        )
        client.force_login(user)
        response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "youtube.com/iframe_api" in content


@pytest.mark.django_db
class TestTalkListView:
    """Verify TalkListView filtering by room, date, track, type, and HTMX fragments."""

    def test_filter_by_room(self, client: Client, user: CustomUser, event: Event) -> None:
        """Show only talks assigned to the selected room."""
        room = baker.make(Room, name="Room 1")
        baker.make(Talk, event=event, title="Talk In Room", room=room)
        baker.make(Talk, event=event, title="Talk No Room", room=None)
        client.force_login(user)
        # event=all isolates the room filter from default-event resolution.
        url = reverse("talk_list") + f"?room={room.pk}&event=all"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Talk In Room" in content
        assert "Talk No Room" not in content

    def test_filter_by_room_ignores_non_numeric(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """A non-numeric ?room= must not raise ValueError and is simply ignored."""
        baker.make(Talk, title="Some Talk", room=None, event=event)
        client.force_login(user)
        response = client.get(reverse("talk_list") + "?room=not-a-number")
        assert response.status_code == HTTPStatus.OK
        assert "Some Talk" in response.content.decode()

    def test_filter_by_date(self, client: Client, user: CustomUser, event: Event) -> None:
        """Show only talks scheduled on the selected date."""
        now = timezone.now()
        local_date = timezone.localdate(now)
        baker.make(Talk, title="Today Talk", start_time=now, event=event)
        client.force_login(user)
        url = reverse("talk_list") + f"?date={local_date}"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Today Talk" in response.content.decode()

    def test_filter_by_track(self, client: Client, user: CustomUser, event: Event) -> None:
        """Show only talks in the selected track."""
        baker.make(Talk, event=event, title="PyData Talk", track="PyData")
        baker.make(Talk, event=event, title="Other Talk", track="Other")
        client.force_login(user)
        url = reverse("talk_list") + "?track=PyData"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "PyData Talk" in content
        assert "Other Talk" not in content

    def test_filter_by_presentation_type(
        self, client: Client, user: CustomUser, event: Event
    ) -> None:
        """Show only talks matching the selected presentation type."""
        baker.make(
            Talk, event=event, title="A Tutorial", presentation_type=Talk.PresentationType.TUTORIAL
        )
        baker.make(
            Talk, event=event, title="A Keynote", presentation_type=Talk.PresentationType.KEYNOTE
        )
        client.force_login(user)
        url = reverse("talk_list") + "?presentation_type=Tutorial"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "A Tutorial" in content
        assert "A Keynote" not in content

    def test_htmx_request_returns_fragment(self, client: Client, user: CustomUser) -> None:
        """Return an HTML fragment without <html> wrapper for HTMX requests."""
        client.force_login(user)
        url = reverse("talk_list")
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "<html>" not in content.lower()

    def test_context_has_filter_options(
        self, client: Client, user: CustomUser, event: Event
    ) -> None:
        """Include rooms, dates, tracks, and types in the template context."""
        baker.make(Talk, event=event, presentation_type=Talk.PresentationType.TALK)
        client.force_login(user)
        url = reverse("talk_list")
        response = client.get(url)
        context = response.context
        assert "rooms" in context
        assert "dates" in context
        assert "tracks" in context
        assert "presentation_types" in context

    def test_search_in_scopes(self, client: Client, user: CustomUser, event: Event) -> None:
        """Test the search_in parameter for scoped search."""
        baker.make(Talk, event=event, title="Find Me Talk")
        client.force_login(user)
        url = reverse("talk_list") + "?q=Find&search_in=title"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_search_in_author_scope(self, client: Client, user: CustomUser, event: Event) -> None:
        """Scope search to the author field when search_in=author is specified."""
        talk = baker.make(Talk, event=event, title="Speaker Test")
        speaker = baker.make(Speaker, name="SpecialSpeakerName")
        talk.speakers.add(speaker)
        client.force_login(user)
        url = reverse("talk_list") + "?q=SpecialSpeakerName&search_in=author"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Speaker Test" in response.content.decode()

    def test_event_switch_refreshes_all_dropdowns_via_htmx(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """
        Switching events via HTMX should OOB-swap all filter dropdowns.

        When a user selects a different event, stale filter values from the
        previous event (room, date, track, type) must be cleared and the
        dropdown options must reflect the newly selected event.
        """
        event_a = baker.make(Event, is_active=True)
        event_b = baker.make(Event, is_active=True)
        user.events.add(event_a, event_b)

        room_a = baker.make(Room, name="Room A")
        room_b = baker.make(Room, name="Room B")

        now = timezone.now()
        tomorrow = now + timedelta(days=1)

        baker.make(
            Talk,
            title="Event A Talk",
            event=event_a,
            room=room_a,
            start_time=now,
            track="Track A",
            presentation_type=Talk.PresentationType.TALK,
        )
        baker.make(
            Talk,
            title="Event B Talk",
            event=event_b,
            room=room_b,
            start_time=tomorrow,
            track="Track B",
            presentation_type=Talk.PresentationType.TUTORIAL,
        )

        # Request Event B while keeping stale filter values from Event A
        client.force_login(user)
        response = client.get(
            reverse("talk_list"),
            {
                "event": str(event_b.pk),
                "room": str(room_a.pk),
                "date": timezone.localdate(now).isoformat(),
                "track": "Track A",
                "presentation_type": Talk.PresentationType.TALK,
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        ctx = response.context

        # All stale selections must be cleared
        assert ctx["selected_room"] == ""
        assert ctx["selected_date"] == ""
        assert ctx["selected_track"] == ""
        assert ctx["selected_type"] == ""

        # OOB swap elements for every dropdown must be present
        oob_attr = 'hx-swap-oob="true"'
        for filter_id in (
            "room-filter",
            "date-filter",
            "track-filter",
            "type-filter",
            "status-filter",
        ):
            assert f'id="{filter_id}"' in content
        assert content.count(oob_attr) == len(("room", "date", "track", "type", "status"))

        # Only Event B data should appear
        assert "Room B" in content
        assert "Track B" in content
        assert "Event B Talk" in content
        assert "Event A Talk" not in content

    def test_all_events_shows_combined_filter_options(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """Selecting "All Events" should show filter options from all events."""
        event_a = baker.make(Event, is_active=True)
        event_b = baker.make(Event, is_active=True)
        user.events.add(event_a, event_b)

        room_a = baker.make(Room, name="Alpha Room")
        room_b = baker.make(Room, name="Beta Room")

        baker.make(Talk, event=event_a, room=room_a, track="ML")
        baker.make(Talk, event=event_b, room=room_b, track="Web")

        client.force_login(user)
        response = client.get(reverse("talk_list"), {"event": "all"})

        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Alpha Room" in content
        assert "Beta Room" in content
        assert "ML" in content
        assert "Web" in content

    def test_combined_filters_with_no_matching_talks(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """Combining valid filters that have no intersection should show no talks."""
        event = baker.make(Event, is_active=True)
        user.events.add(event)

        room_a = baker.make(Room, name="Room A")
        room_b = baker.make(Room, name="Room B")

        now = timezone.now()
        tomorrow = now + timedelta(days=1)

        # Room A has talks only today; Room B has talks only tomorrow.
        baker.make(Talk, title="Room A Today", event=event, room=room_a, start_time=now)
        baker.make(Talk, title="Room B Tomorrow", event=event, room=room_b, start_time=tomorrow)

        # Select Room A + tomorrow's date: valid individually, empty together.
        client.force_login(user)
        response = client.get(
            reverse("talk_list"),
            {
                "event": str(event.pk),
                "room": str(room_a.pk),
                "date": timezone.localdate(tomorrow).isoformat(),
            },
        )

        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Room A Today" not in content
        assert "Room B Tomorrow" not in content
        assert "No talks found" in content

    def test_talk_list_no_n_plus_one(self, client: Client, user: CustomUser) -> None:
        """
        Rendering a busy talk_list page must not fan out to per-row queries.

        Covers the speakers prefetch, the rating-stats annotation, and the
        per-row ``get_video_link`` / ``get_transcription_url`` template calls.
        """
        event = baker.make(Event, is_active=True)
        user.events.add(event)
        room = baker.make(Room)
        speaker = baker.make(Speaker, name="Some Speaker")
        for i in range(8):
            talk = baker.make(
                Talk,
                title=f"Listed Talk {i}",
                event=event,
                room=room,
                start_time=timezone.now() + timedelta(hours=i),
                duration=timedelta(minutes=30),
                video_link="",
            )
            talk.speakers.add(speaker)

        client.force_login(user)
        with assert_no_n_plus_one():
            response = client.get(reverse("talk_list"))
        assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
class TestDashboardStats:
    """Tests for dashboard_stats view."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> Generator[None]:
        """Clear Django cache around tests to avoid @cache_page interference."""
        cache.clear()
        yield
        cache.clear()

    def test_dashboard_stats(self, client: Client, user: CustomUser) -> None:
        """Return aggregated statistics scoped to the user's events."""
        event = baker.make(Event, is_active=True)
        user.events.add(event)
        baker.make(Talk, event=event, _quantity=3)
        client.force_login(user)
        url = reverse("dashboard_stats")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "3" in content

    def test_dashboard_stats_excludes_other_events(self, client: Client, user: CustomUser) -> None:
        """Stats should not include talks from events the user has no access to."""
        my_event = baker.make(Event, is_active=True)
        other_event = baker.make(Event, is_active=True)
        user.events.add(my_event)
        baker.make(Talk, event=my_event, _quantity=2)
        baker.make(Talk, event=other_event, _quantity=5)
        client.force_login(user)
        url = reverse("dashboard_stats")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        # Should show 2 (my_event), not 7 (total)
        assert "2" in content

    def test_dashboard_stats_superuser_sees_all(self, client: Client, event: Event) -> None:
        """Superusers should see stats from all active events."""
        superuser = baker.make(CustomUser, is_superuser=True, email="admin@example.com")
        event_a = baker.make(Event, is_active=True)
        event_b = baker.make(Event, is_active=True)
        baker.make(Talk, event=event_a, _quantity=3)
        baker.make(Talk, event=event_b, _quantity=4)
        client.force_login(superuser)
        url = reverse("dashboard_stats")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "7" in content

    def test_dashboard_stats_cache_varies_by_user(self, client: Client, event: Event) -> None:
        """Different users must get different cached responses."""
        event_a = baker.make(Event, name="Alpha Conf", is_active=True)
        event_b = baker.make(Event, name="Beta Conf", is_active=True)
        baker.make(Talk, event=event_a, _quantity=2)
        baker.make(Talk, event=event_b, _quantity=5)

        user_a = baker.make(CustomUser, email="a@example.com")
        user_a.events.add(event_a)
        user_b = baker.make(CustomUser, email="b@example.com")
        user_b.events.add(event_b)

        client.force_login(user_a)
        resp_a = client.get(reverse("dashboard_stats"))
        content_a = resp_a.content.decode()
        assert "2" in content_a

        client.force_login(user_b)
        resp_b = client.get(reverse("dashboard_stats"))
        content_b = resp_b.content.decode()
        assert "5" in content_b


@pytest.mark.django_db
class TestUpcomingTalks:
    """Tests for upcoming_talks view."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> Generator[None]:
        """Clear Django cache around tests to avoid @cache_page interference."""
        cache.clear()
        yield
        cache.clear()

    def test_upcoming_talks(self, client: Client, user: CustomUser) -> None:
        """Return only talks scheduled in the future for the user's events."""
        event = baker.make(Event, is_active=True)
        user.events.add(event)
        future = timezone.now() + timedelta(hours=2)
        baker.make(Talk, title="Upcoming Test", start_time=future, event=event)
        client.force_login(user)
        url = reverse("upcoming_talks")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Upcoming Test" in response.content.decode()

    def test_upcoming_talks_excludes_other_events(self, client: Client, user: CustomUser) -> None:
        """Upcoming talks should not include talks from events the user cannot access."""
        my_event = baker.make(Event, is_active=True)
        other_event = baker.make(Event, is_active=True)
        user.events.add(my_event)
        future = timezone.now() + timedelta(hours=2)
        baker.make(Talk, title="My Talk", start_time=future, event=my_event)
        baker.make(Talk, title="Hidden Talk", start_time=future, event=other_event)
        client.force_login(user)
        url = reverse("upcoming_talks")
        response = client.get(url)
        content = response.content.decode()
        assert "My Talk" in content
        assert "Hidden Talk" not in content

    def test_upcoming_talks_no_n_plus_one(self, client: Client, user: CustomUser) -> None:
        """Eight upcoming talks must not trigger a query per row in the template."""
        event = baker.make(Event, is_active=True)
        user.events.add(event)
        room = baker.make(Room)
        future = timezone.now() + timedelta(hours=2)
        for i in range(8):
            baker.make(
                Talk,
                title=f"Future Talk {i}",
                start_time=future + timedelta(minutes=30 * i),
                duration=timedelta(minutes=30),
                event=event,
                room=room,
                video_link="",
            )

        client.force_login(user)
        with assert_no_n_plus_one():
            response = client.get(reverse("upcoming_talks"))
        assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
class TestTalkRedirectView:
    """Tests for talk_redirect_view."""

    def test_redirect_by_pk(self, client: Client, user: CustomUser, event: Event) -> None:
        # Note: numeric talk IDs match <int:pk>/ (TalkDetailView) first,
        # so talk_redirect_view only handles non-numeric (pretalx) IDs.
        # We test with a pretalx-style ID that resolves via pk fallback.
        """Redirect to the talk detail page when found by pretalx ID."""
        talk = baker.make(Talk, event=event, pretalx_link="https://pretalx.com/event/talk/DEMO1")
        client.force_login(user)
        url = reverse("talk_redirect", args=["DEMO1"])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
        assert str(talk.pk) in response.headers["Location"]

    def test_redirect_by_pretalx_id(self, client: Client, user: CustomUser, event: Event) -> None:
        """Redirect when the talk is found by its pretalx link slug."""
        baker.make(
            Talk,
            event=event,
            pretalx_link="https://pretalx.com/event/talk/TEST1",
        )
        client.force_login(user)
        url = reverse("talk_redirect", args=["TEST1"])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND

    def test_redirect_not_found(self, client: Client, user: CustomUser) -> None:
        """Return 404 when no talk matches the given slug."""
        client.force_login(user)
        url = reverse("talk_redirect", args=["NONEXISTENT"])
        response = client.get(url)
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
class TestStatusFilter:
    """Verify the ?status= filter splits talks into current/upcoming/completed."""

    def _make_talks(self, event: Event) -> dict[str, Talk]:
        """Build one talk for each timing bucket, all within the same event."""
        now = timezone.now()
        return {
            "current": baker.make(
                Talk,
                title="LiveTimingTalk",
                event=event,
                start_time=now - timedelta(minutes=10),
                duration=timedelta(minutes=30),
            ),
            "upcoming": baker.make(
                Talk,
                title="FutureTimingTalk",
                event=event,
                start_time=now + timedelta(hours=2),
                duration=timedelta(minutes=30),
            ),
            "completed": baker.make(
                Talk,
                title="PastTimingTalk",
                event=event,
                start_time=now - timedelta(hours=3),
                duration=timedelta(minutes=30),
            ),
        }

    @pytest.mark.parametrize(
        ("status", "visible", "hidden"),
        [
            ("current", "LiveTimingTalk", ("FutureTimingTalk", "PastTimingTalk")),
            ("upcoming", "FutureTimingTalk", ("LiveTimingTalk", "PastTimingTalk")),
            ("completed", "PastTimingTalk", ("LiveTimingTalk", "FutureTimingTalk")),
        ],
    )
    def test_status_filter(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
        status: str,
        visible: str,
        hidden: tuple[str, ...],
    ) -> None:
        """Each status keyword shows only talks in that bucket."""
        self._make_talks(event)
        client.force_login(user)
        response = client.get(reverse("talk_list"), {"status": status})
        content = response.content.decode()
        assert visible in content
        for title in hidden:
            assert title not in content

    def test_unknown_status_shows_all(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """An unrecognized status string leaves the queryset unfiltered."""
        self._make_talks(event)
        client.force_login(user)
        response = client.get(reverse("talk_list"), {"status": "weird"})
        content = response.content.decode()
        for title in ("LiveTimingTalk", "FutureTimingTalk", "PastTimingTalk"):
            assert title in content


@pytest.mark.django_db
class TestDashboardStatsRecorded:
    """The 'recorded' column counts talks that resolve to a video link."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> Generator[None]:
        """Clear Django cache around tests to avoid @cache_page interference."""
        cache.clear()
        yield
        cache.clear()

    @override_settings(SHOW_UPCOMING_TALKS_LINKS=True)
    def test_recorded_counts_only_talks_with_video(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """Only talks whose ``get_video_link`` resolves non-empty count as recorded."""
        event = baker.make(Event, is_active=True)
        user.events.add(event)
        past = timezone.now() - timedelta(hours=2)
        baker.make(
            Talk,
            event=event,
            start_time=past,
            video_link="https://vimeo.com/1",
            room=None,
        )
        baker.make(Talk, event=event, start_time=past, video_link="", room=None)
        client.force_login(user)
        response = client.get(reverse("dashboard_stats"))
        assert response.status_code == HTTPStatus.OK
        # One recorded out of two talks.
        content = response.content.decode()
        assert "1" in content

    def test_dashboard_stats_no_n_plus_one(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """Dashboard stats must not fan out to one Streaming query per Talk."""
        event = baker.make(Event, is_active=True)
        user.events.add(event)
        room = baker.make(Room)
        now = timezone.now()
        for i in range(10):
            baker.make(
                Talk,
                event=event,
                room=room,
                start_time=now + timedelta(hours=i),
                duration=timedelta(minutes=30),
                video_link="",
            )

        client.force_login(user)
        with assert_no_n_plus_one():
            response = client.get(reverse("dashboard_stats"))
        assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
class TestRateTalkHTMX:
    """HTMX paths through rate_talk return fragments, not redirects."""

    def test_htmx_rating_returns_widget(
        self, client: Client, user: CustomUser, event: Event
    ) -> None:
        """A successful HTMX rating returns both the widget and the OOB title stars."""
        talk = baker.make(Talk, event=event, title="HTMX Talk")
        client.force_login(user)
        response = client.post(
            reverse("rate_talk", args=[talk.pk]),
            {"score": "5"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        # Widget + out-of-band stars both render.
        assert "rating-widget" in content
        assert "hx-swap-oob" in content

    def test_htmx_invalid_score_returns_422(
        self, client: Client, user: CustomUser, event: Event
    ) -> None:
        """Non-numeric scores on the HTMX path come back as a plain 422."""
        talk = baker.make(Talk, event=event)
        client.force_login(user)
        response = client.post(
            reverse("rate_talk", args=[talk.pk]),
            {"score": "abc"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_htmx_score_out_of_range_returns_422(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """HTMX score=6 triggers the range branch of the validator."""
        talk = baker.make(Talk, event=event)
        client.force_login(user)
        response = client.post(
            reverse("rate_talk", args=[talk.pk]),
            {"score": "42"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_htmx_comment_save_persists_and_keeps_score(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
    ) -> None:
        """Saving a comment via HTMX stores the stripped comment without replaying defaults."""
        talk = baker.make(Talk, event=event)
        client.force_login(user)
        # First, star-only click (no comment change).
        client.post(
            reverse("rate_talk", args=[talk.pk]),
            {"score": "3", "comment": "Typed but not saved"},
            HTTP_HX_REQUEST="true",
        )
        # Then explicitly save the comment.
        response = client.post(
            reverse("rate_talk", args=[talk.pk]),
            {"score": "3", "comment": "  Final comment  ", "save_comment": "1"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK

        rating = Rating.objects.get(talk=talk, user=user)
        assert rating.score == 3
        assert rating.comment == "Final comment"

    def test_htmx_integrity_error_returns_500(
        self,
        client: Client,
        user: CustomUser,
        event: Event,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A DB-level integrity violation during save becomes a 500 for HTMX clients."""
        talk = baker.make(Talk, event=event)

        def boom(**_kwargs: object) -> None:
            msg = "unique constraint"
            raise IntegrityError(msg)

        monkeypatch.setattr(Rating.objects, "update_or_create", boom)
        client.force_login(user)
        response = client.post(
            reverse("rate_talk", args=[talk.pk]),
            {"score": "4"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
