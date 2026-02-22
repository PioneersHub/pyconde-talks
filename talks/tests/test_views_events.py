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

    def test_user_sees_talks_without_event(
        self,
        client: Client,
        user_event_a: CustomUser,
    ) -> None:
        """User sees talks that have no event assigned (event=None)."""
        orphan_talk = baker.make(Talk, title="Orphan Talk", event=None)
        client.force_login(user_event_a)
        response = client.get(reverse("talk_list"))
        assert orphan_talk.title in response.content.decode()

    def test_superuser_sees_all_events(
        self,
        client: Client,
        superuser: CustomUser,
        event_a: Event,
        event_b: Event,
    ) -> None:
        """Superuser sees talks from all events."""
        talk_a = baker.make(Talk, title="Talk Alpha", event=event_a)
        talk_b = baker.make(Talk, title="Talk Beta", event=event_b)
        client.force_login(superuser)
        response = client.get(reverse("talk_list"))
        content = response.content.decode()
        assert talk_a.title in content
        assert talk_b.title in content


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
