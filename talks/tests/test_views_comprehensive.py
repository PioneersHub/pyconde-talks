"""Tests for talks.views."""

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from talks.models import Room, Speaker, Talk
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.fixture()
def user() -> CustomUser:
    """Create a regular user for testing."""
    return baker.make(CustomUser, email="viewer@example.com")


@pytest.mark.django_db
class TestTalkDetailView:
    """Tests for TalkDetailView."""

    def test_authenticated_user_can_view_detail(self, client: Client, user: CustomUser) -> None:
        """Allow authenticated users to view a talk's detail page."""
        talk = baker.make(Talk, title="Detail Talk")
        client.force_login(user)
        url = reverse("talk_detail", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Detail Talk" in response.content.decode()

    def test_unauthenticated_user_redirected(self, client: Client) -> None:
        """Redirect unauthenticated users to the login page."""
        talk = baker.make(Talk)
        url = reverse("talk_detail", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND

    def test_nonexistent_talk_404(self, client: Client, user: CustomUser) -> None:
        """Return 404 when the requested talk PK does not exist."""
        client.force_login(user)
        url = reverse("talk_detail", args=[99999])
        response = client.get(url)
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
class TestTalkListView:
    """Verify TalkListView filtering by room, date, track, type, and HTMX fragments."""

    def test_filter_by_room(self, client: Client, user: CustomUser) -> None:
        """Show only talks assigned to the selected room."""
        room = baker.make(Room, name="Room 1")
        baker.make(Talk, title="Talk In Room", room=room)
        baker.make(Talk, title="Talk No Room", room=None)
        client.force_login(user)
        url = reverse("talk_list") + f"?room={room.pk}"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Talk In Room" in content
        assert "Talk No Room" not in content

    def test_filter_by_date(self, client: Client, user: CustomUser) -> None:
        """Show only talks scheduled on the selected date."""
        today = timezone.now()
        baker.make(Talk, title="Today Talk", start_time=today)
        client.force_login(user)
        url = reverse("talk_list") + f"?date={today.strftime('%Y-%m-%d')}"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Today Talk" in response.content.decode()

    def test_filter_by_track(self, client: Client, user: CustomUser) -> None:
        """Show only talks in the selected track."""
        baker.make(Talk, title="PyData Talk", track="PyData")
        baker.make(Talk, title="Other Talk", track="Other")
        client.force_login(user)
        url = reverse("talk_list") + "?track=PyData"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "PyData Talk" in content
        assert "Other Talk" not in content

    def test_filter_by_presentation_type(self, client: Client, user: CustomUser) -> None:
        """Show only talks matching the selected presentation type."""
        baker.make(Talk, title="A Tutorial", presentation_type=Talk.PresentationType.TUTORIAL)
        baker.make(Talk, title="A Keynote", presentation_type=Talk.PresentationType.KEYNOTE)
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

    def test_context_has_filter_options(self, client: Client, user: CustomUser) -> None:
        """Include rooms, dates, tracks, and types in the template context."""
        baker.make(Talk, presentation_type=Talk.PresentationType.TALK)
        client.force_login(user)
        url = reverse("talk_list")
        response = client.get(url)
        context = response.context
        assert "rooms" in context
        assert "dates" in context
        assert "tracks" in context
        assert "presentation_types" in context

    def test_search_in_scopes(self, client: Client, user: CustomUser) -> None:
        """Test the search_in parameter for scoped search."""
        baker.make(Talk, title="Find Me Talk")
        client.force_login(user)
        url = reverse("talk_list") + "?q=Find&search_in=title"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_search_in_author_scope(self, client: Client, user: CustomUser) -> None:
        """Scope search to the author field when search_in=author is specified."""
        talk = baker.make(Talk, title="Speaker Test")
        speaker = baker.make(Speaker, name="SpecialSpeakerName")
        talk.speakers.add(speaker)
        client.force_login(user)
        url = reverse("talk_list") + "?q=SpecialSpeakerName&search_in=author"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Speaker Test" in response.content.decode()


@pytest.mark.django_db
class TestDashboardStats:
    """Tests for dashboard_stats view."""

    def test_dashboard_stats(self, client: Client, user: CustomUser) -> None:
        """Return aggregated statistics for the dashboard."""
        baker.make(Talk, _quantity=3)
        client.force_login(user)
        url = reverse("dashboard_stats")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
class TestUpcomingTalks:
    """Tests for upcoming_talks view."""

    def test_upcoming_talks(self, client: Client, user: CustomUser) -> None:
        """Return only talks scheduled in the future."""
        future = timezone.now() + timedelta(hours=2)
        baker.make(Talk, title="Upcoming Test", start_time=future)
        client.force_login(user)
        url = reverse("upcoming_talks")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
class TestTalkRedirectView:
    """Tests for talk_redirect_view."""

    def test_redirect_by_pk(self, client: Client, user: CustomUser) -> None:
        # Note: numeric talk IDs match <int:pk>/ (TalkDetailView) first,
        # so talk_redirect_view only handles non-numeric (pretalx) IDs.
        # We test with a pretalx-style ID that resolves via pk fallback.
        """Redirect to the talk detail page when found by pretalx ID."""
        talk = baker.make(Talk, pretalx_link="https://pretalx.com/event/talk/DEMO1")
        client.force_login(user)
        url = reverse("talk_redirect", args=["DEMO1"])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
        assert str(talk.pk) in response.headers["Location"]

    def test_redirect_by_pretalx_id(self, client: Client, user: CustomUser) -> None:
        """Redirect when the talk is found by its pretalx link slug."""
        baker.make(
            Talk,
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
