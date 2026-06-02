"""Tests for the moderator-only session-chair feature: model, block toggle, and day grid."""

from datetime import datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.models import Room, Talk
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


# Talks/rooms are event-scoped and access is gated on event membership, so every fixture shares
# one active event and the moderators join it. Mirrors talks/tests/test_schedule.py.
def _morning() -> datetime:
    """Return today at 10:00 so date resolution is stable and never straddles midnight."""
    return timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def event() -> Event:
    """Return the single active event the chair fixtures share (talks/rooms are event-scoped)."""
    return Event.objects.create(slug="chair", name="Chair", year=2099)


@pytest.fixture()
def moderator(event: Event) -> CustomUser:
    """Create a moderator (staff) user with access to the chair event."""
    user = baker.make(CustomUser, email="mod@example.com", is_staff=True)
    user.events.add(event)
    return user


@pytest.fixture()
def other_moderator(event: Event) -> CustomUser:
    """Create a second moderator with access to the chair event."""
    user = baker.make(CustomUser, email="mod2@example.com", is_staff=True)
    user.events.add(event)
    return user


@pytest.fixture()
def regular_user(event: Event) -> CustomUser:
    """Create a non-moderator user with access to the chair event."""
    user = baker.make(CustomUser, email="user@example.com", is_staff=False)
    user.events.add(event)
    return user


@pytest.fixture()
def room(event: Event) -> Room:
    """Create a room in the chair event."""
    return baker.make(Room, name="Main Hall", event=event)


@pytest.fixture()
def talk(room: Room, event: Event) -> Talk:
    """Create a single talk in the shared event."""
    return baker.make(
        Talk,
        title="Chair Test Talk",
        room=room,
        event=event,
        start_time=_morning(),
        duration=timedelta(minutes=30),
    )


# ---------------------------------------------------------------------------
# Model property tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestChairDisplayName:
    """Tests for Talk.chair_display_name."""

    def test_empty_when_unassigned(self, talk: Talk) -> None:
        """An unassigned session returns an empty string."""
        assert talk.chair_display_name == ""

    def test_prefers_display_name(self, talk: Talk) -> None:
        """The chosen display name wins over other fields."""
        chair = baker.make(CustomUser, email="x@example.com", display_name="Ada L.")
        talk.session_chair = chair
        assert talk.chair_display_name == "Ada L."

    def test_falls_back_to_full_name(self, talk: Talk) -> None:
        """Without a display name, the full name is used."""
        chair = baker.make(
            CustomUser,
            email="x@example.com",
            display_name="",
            first_name="Grace",
            last_name="Hopper",
        )
        talk.session_chair = chair
        assert talk.chair_display_name == "Grace Hopper"

    def test_falls_back_to_plain_email(self, talk: Talk) -> None:
        """Without any name, the plain (non-obfuscated) email is shown."""
        chair = baker.make(CustomUser, email="plain@example.com", display_name="")
        talk.session_chair = chair
        assert talk.chair_display_name == "plain@example.com"


# ---------------------------------------------------------------------------
# toggle_session_chair view tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestToggleSessionChair:
    """Tests for the toggle_session_chair view."""

    def test_non_moderator_forbidden(
        self,
        client: Client,
        regular_user: CustomUser,
        talk: Talk,
    ) -> None:
        """A non-moderator cannot assign a chair."""
        client.force_login(regular_user)
        url = reverse("toggle_session_chair", args=[talk.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_claim_single_talk(
        self,
        client: Client,
        moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """A moderator can claim a standalone session."""
        client.force_login(moderator)
        url = reverse("toggle_session_chair", args=[talk.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        talk.refresh_from_db()
        assert talk.session_chair_id == moderator.pk

    def test_claim_assigns_whole_block(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Claiming one talk chairs the whole back-to-back block."""
        start = _morning()
        first = baker.make(
            Talk,
            title="Block 1",
            room=room,
            event=event,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        second = baker.make(
            Talk,
            title="Block 2",
            room=room,
            event=event,
            start_time=start + timedelta(minutes=30),
            duration=timedelta(minutes=30),
        )
        # A talk much later in the same room is a separate block.
        far = baker.make(
            Talk,
            title="Later",
            room=room,
            event=event,
            start_time=start + timedelta(hours=3),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        client.post(reverse("toggle_session_chair", args=[first.pk]))
        first.refresh_from_db()
        second.refresh_from_db()
        far.refresh_from_db()
        assert first.session_chair_id == moderator.pk
        assert second.session_chair_id == moderator.pk
        assert far.session_chair_id is None

    def test_release_clears_whole_block(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Stepping down clears the whole block the moderator chaired."""
        start = _morning()
        first = baker.make(
            Talk,
            title="Block 1",
            room=room,
            event=event,
            start_time=start,
            duration=timedelta(minutes=30),
            session_chair=moderator,
        )
        second = baker.make(
            Talk,
            title="Block 2",
            room=room,
            event=event,
            start_time=start + timedelta(minutes=30),
            duration=timedelta(minutes=30),
            session_chair=moderator,
        )
        client.force_login(moderator)
        client.post(reverse("toggle_session_chair", args=[second.pk]))
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.session_chair_id is None
        assert second.session_chair_id is None

    def test_cannot_override_another_chair(
        self,
        client: Client,
        moderator: CustomUser,
        other_moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """A moderator cannot take over a block chaired by someone else."""
        talk.session_chair = other_moderator
        talk.save(update_fields=["session_chair"])
        client.force_login(moderator)
        url = reverse("toggle_session_chair", args=[talk.pk])
        client.post(url)
        talk.refresh_from_db()
        assert talk.session_chair_id == other_moderator.pk

    def test_htmx_returns_grid_table(
        self,
        client: Client,
        moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """An HTMX request gets the grid table fragment back, not a redirect."""
        client.force_login(moderator)
        url = reverse("toggle_session_chair", args=[talk.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        assert b"chair-grid-table" in response.content

    def test_no_event_access_returns_404(
        self,
        client: Client,
        moderator: CustomUser,
    ) -> None:
        """A moderator cannot chair a talk from an event they cannot access."""
        other_event = baker.make(Event, is_active=True)
        restricted = baker.make(Talk, event=other_event, start_time=_morning())
        client.force_login(moderator)
        url = reverse("toggle_session_chair", args=[restricted.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# chair_grid_view tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestChairGridView:
    """Tests for the chair_grid_view."""

    def test_non_moderator_forbidden(
        self,
        client: Client,
        regular_user: CustomUser,
    ) -> None:
        """A non-moderator cannot open the chair grid."""
        client.force_login(regular_user)
        response = client.get(reverse("chair_grid"))
        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_renders_grid_with_chair_button(
        self,
        client: Client,
        moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """The grid renders an unassigned talk with a Chair button."""
        client.force_login(moderator)
        response = client.get(reverse("chair_grid"))
        assert response.status_code == HTTPStatus.OK
        assert b"Chair Test Talk" in response.content
        assert b"Chair" in response.content

    def test_does_not_leak_other_event(
        self,
        client: Client,
        moderator: CustomUser,
    ) -> None:
        """Passing another event's id must not reveal its talks."""
        other_event = baker.make(Event, is_active=True)
        hidden_room = baker.make(Room, name="Hidden Hall", event=other_event)
        when = _morning()
        baker.make(
            Talk,
            title="Secret Session",
            room=hidden_room,
            event=other_event,
            start_time=when,
        )
        client.force_login(moderator)
        response = client.get(
            reverse("chair_grid"),
            {"event": str(other_event.pk), "date": when.date().isoformat()},
        )
        assert response.status_code == HTTPStatus.OK
        assert b"Secret Session" not in response.content

    def test_shows_chair_name(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """An assigned chair's name appears in the grid."""
        chair = baker.make(CustomUser, email="z@example.com", display_name="Linus T.")
        baker.make(
            Talk,
            title="Chaired Session",
            room=room,
            event=event,
            session_chair=chair,
            start_time=_morning(),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.get(reverse("chair_grid"))
        assert response.status_code == HTTPStatus.OK
        assert b"Linus T." in response.content

    def test_block_talks_share_block_id(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Back-to-back talks in a room share a data-block id so the UI can highlight them."""
        start = _morning()
        baker.make(
            Talk,
            title="Block A",
            room=room,
            event=event,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        baker.make(
            Talk,
            title="Block B",
            room=room,
            event=event,
            start_time=start + timedelta(minutes=30),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.get(reverse("chair_grid"), {"date": start.date().isoformat()})
        content = response.content.decode()
        # Both talks belong to the first block of their room (index 0).
        talks_in_block = 2
        assert content.count(f'data-block="{room.pk}-0"') == talks_in_block

    def test_my_blocks_are_marked(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Cells chaired by the current user carry the persistent highlight class."""
        baker.make(
            Talk,
            title="Mine",
            room=room,
            event=event,
            session_chair=moderator,
            start_time=_morning(),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.get(reverse("chair_grid"))
        assert b"chair-mine" in response.content


# ---------------------------------------------------------------------------
# Talk detail page tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestChairOnDetailPage:
    """Tests for the read-only chair name on the talk detail page."""

    def test_moderator_sees_chair_name(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """A moderator sees the assigned chair on the detail page."""
        chair = baker.make(CustomUser, email="z@example.com", display_name="Chair Person")
        detail_talk = baker.make(
            Talk,
            title="Detail Talk",
            room=room,
            event=event,
            session_chair=chair,
            start_time=_morning(),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.get(reverse("talk_detail", args=[detail_talk.pk]))
        assert response.status_code == HTTPStatus.OK
        assert b"Session chair" in response.content
        assert b"Chair Person" in response.content

    def test_regular_user_does_not_see_chair_name(
        self,
        client: Client,
        regular_user: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """A regular user never sees the session chair on the detail page."""
        chair = baker.make(CustomUser, email="z@example.com", display_name="Chair Person")
        detail_talk = baker.make(
            Talk,
            title="Detail Talk",
            room=room,
            event=event,
            session_chair=chair,
            start_time=_morning(),
            duration=timedelta(minutes=30),
        )
        client.force_login(regular_user)
        response = client.get(reverse("talk_detail", args=[detail_talk.pk]))
        assert response.status_code == HTTPStatus.OK
        assert b"Session chair" not in response.content
