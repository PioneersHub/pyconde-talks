"""Tests for event-based filtering in talk views and login view event integration."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.fixture()
def event_a() -> Event:
    """Create Event A."""
    return Event.objects.create(name="Event A", slug="event-a", year=2025)


@pytest.fixture()
def event_b() -> Event:
    """Create Event B."""
    return Event.objects.create(name="Event B", slug="event-b", year=2025)


@pytest.fixture()
def user_event_a(event_a: Event) -> CustomUser:
    """Create a regular user linked to Event A only."""
    user = baker.make(CustomUser, email="user-a@example.com")
    user.events.add(event_a)
    return user


@pytest.fixture()
def superuser() -> CustomUser:
    """Create a superuser (sees all events)."""
    return CustomUser.objects.create_superuser(
        email="admin@example.com",
        password="password",
    )


@pytest.mark.django_db
class TestTalkListEventFiltering:
    """Verify TalkListView restricts talks based on user's event associations."""

    def test_user_sees_own_event_talks(
        self,
        client: Client,
        user_event_a: CustomUser,
        event_a: Event,
        event_b: Event,
    ) -> None:
        """User linked to Event A sees Event A talks, not Event B talks."""
        talk_a = baker.make(Talk, title="Talk A", event=event_a)
        talk_b = baker.make(Talk, title="Talk B", event=event_b)
        client.force_login(user_event_a)
        response = client.get(reverse("talk_list"))
        content = response.content.decode()
        assert talk_a.title in content
        assert talk_b.title not in content

    def test_all_events_shows_accessible_talks(
        self,
        client: Client,
        user_event_a: CustomUser,
        event_a: Event,
    ) -> None:
        """Selecting 'All Events' shows the user's accessible talks."""
        talk = baker.make(Talk, title="Event A Talk", event=event_a)
        client.force_login(user_event_a)
        response = client.get(reverse("talk_list"), {"event": "all"})
        assert talk.title in response.content.decode()

    def test_superuser_sees_all_events(
        self,
        client: Client,
        superuser: CustomUser,
        event_a: Event,
        event_b: Event,
    ) -> None:
        """Superuser sees talks from all events when selecting 'All Events'."""
        talk_a = baker.make(Talk, title="Talk Alpha", event=event_a)
        talk_b = baker.make(Talk, title="Talk Beta", event=event_b)
        client.force_login(superuser)
        response = client.get(reverse("talk_list"), {"event": "all"})
        content = response.content.decode()
        assert talk_a.title in content
        assert talk_b.title in content

    def test_non_digit_event_param_is_ignored(
        self,
        client: Client,
        user_event_a: CustomUser,
        event_a: Event,
    ) -> None:
        """Garbage ``?event=`` values must not raise ValueError from the filter."""
        baker.make(Talk, title="Talk A", event=event_a)
        client.force_login(user_event_a)
        response = client.get(reverse("talk_list"), {"event": "not-a-number"})
        assert response.status_code == HTTPStatus.OK

    def test_non_digit_event_param_falls_back_to_default(
        self,
        client: Client,
        superuser: CustomUser,
        event_a: Event,
        event_b: Event,
        settings: pytest.fixture,  # type: ignore[type-arg,valid-type]
    ) -> None:
        """A garbage ``?event=`` value falls back to the default event, not 'all events'."""
        settings.DEFAULT_EVENT = event_a.slug
        default_talk = baker.make(Talk, title="Default Event Talk", event=event_a)
        other_talk = baker.make(Talk, title="Other Event Talk", event=event_b)
        client.force_login(superuser)
        response = client.get(reverse("talk_list"), {"event": "not-a-number"})
        content = response.content.decode()
        assert response.status_code == HTTPStatus.OK
        assert default_talk.title in content
        # Garbage must behave like "no selection" (default event), not unfiltered.
        assert other_talk.title not in content


@pytest.mark.django_db
class TestTalkDetailEventFiltering:
    """Verify TalkDetailView restricts access based on event association."""

    def test_user_can_view_own_event_talk(
        self,
        client: Client,
        user_event_a: CustomUser,
        event_a: Event,
    ) -> None:
        """User linked to Event A can view detail of Event A talk."""
        talk = baker.make(Talk, title="Visible Talk", event=event_a)
        client.force_login(user_event_a)
        response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK

    def test_user_cannot_view_other_event_talk(
        self,
        client: Client,
        user_event_a: CustomUser,
        event_b: Event,
    ) -> None:
        """User linked to Event A gets 404 for Event B talk."""
        talk = baker.make(Talk, title="Hidden Talk", event=event_b)
        client.force_login(user_event_a)
        response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_superuser_can_view_any_event_talk(
        self,
        client: Client,
        superuser: CustomUser,
        event_b: Event,
    ) -> None:
        """Superuser can view talks from any event."""
        talk = baker.make(Talk, title="Any Talk", event=event_b)
        client.force_login(superuser)
        response = client.get(reverse("talk_detail", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
class TestLoginViewEventContext:
    """Verify the login view provides event context and handles event selection."""

    def test_login_page_includes_active_events(self, client: Client) -> None:
        """Login page context includes active events."""
        active = Event.objects.create(
            name="Active",
            slug="active",
            year=2025,
            is_active=True,
        )
        Event.objects.create(name="Inactive", slug="inactive", year=2025, is_active=False)
        response = client.get(reverse("account_login"))
        events = list(response.context["events"])
        assert active in events
        slugs = [e.slug for e in events]
        assert "inactive" not in slugs

    def test_login_page_default_event_slug(
        self,
        client: Client,
        settings: object,
    ) -> None:
        """Login page context includes DEFAULT_EVENT."""
        settings.DEFAULT_EVENT = "my-default"  # type: ignore[attr-defined]
        response = client.get(reverse("account_login"))
        assert response.context["default_event_slug"] == "my-default"
