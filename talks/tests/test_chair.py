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
def admin_user(event: Event) -> CustomUser:
    """Create a superuser with access to the chair event."""
    user = CustomUser.objects.create_superuser(
        email="admin@example.com",
        password="chair-grid-2099!",
    )
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

    def test_claim_assigns_only_clicked_talk(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Claiming one talk does not affect adjacent talks in the same room."""
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
        client.force_login(moderator)
        client.post(reverse("toggle_session_chair", args=[first.pk]))
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.session_chair_id == moderator.pk
        assert second.session_chair_id is None

    def test_release_clears_only_clicked_talk(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Stepping down clears only the clicked talk, not the entire block."""
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
        assert first.session_chair_id == moderator.pk
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


# ---------------------------------------------------------------------------
# Conflict detection tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestChairConflict:
    """The same person cannot chair two sessions that overlap in time."""

    def test_conflict_blocks_self_claim(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """A moderator already chairing a session cannot claim another one at the same time."""
        other_room = baker.make(Room, name="Side Hall", event=event)
        start = _morning()
        # Moderator already chairs a talk in the other room at 10:00.
        baker.make(
            Talk,
            room=other_room,
            event=event,
            session_chair=moderator,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        # Try to claim a parallel talk in the main room.
        parallel = baker.make(
            Talk,
            room=room,
            event=event,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        client.post(reverse("toggle_session_chair", args=[parallel.pk]))
        parallel.refresh_from_db()
        assert parallel.session_chair_id is None

    def test_htmx_conflict_returns_error_message(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """An HTMX conflict response embeds the error message in the grid fragment."""
        other_room = baker.make(Room, name="Side Hall", event=event)
        start = _morning()
        baker.make(
            Talk,
            title="Existing Session",
            room=other_room,
            event=event,
            session_chair=moderator,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        parallel = baker.make(
            Talk,
            room=room,
            event=event,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.post(
            reverse("toggle_session_chair", args=[parallel.pk]),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK
        assert b"already chairs" in response.content

    def test_non_overlapping_same_day_is_allowed(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """A moderator may chair two sessions on the same day as long as they do not overlap."""
        other_room = baker.make(Room, name="Side Hall", event=event)
        start = _morning()
        baker.make(
            Talk,
            room=other_room,
            event=event,
            session_chair=moderator,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        later = baker.make(
            Talk,
            room=room,
            event=event,
            start_time=start + timedelta(hours=2),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        client.post(reverse("toggle_session_chair", args=[later.pk]))
        later.refresh_from_db()
        assert later.session_chair_id == moderator.pk

    def test_partial_overlap_is_a_conflict(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Even a partial time overlap blocks the claim (e.g. 10:00-11:00 vs 10:45-11:30)."""
        other_room = baker.make(Room, name="Side Hall", event=event)
        start = _morning()
        baker.make(
            Talk,
            room=other_room,
            event=event,
            session_chair=moderator,
            start_time=start,
            duration=timedelta(minutes=60),
        )
        overlapping = baker.make(
            Talk,
            room=room,
            event=event,
            start_time=start + timedelta(minutes=45),
            duration=timedelta(minutes=45),
        )
        client.force_login(moderator)
        client.post(reverse("toggle_session_chair", args=[overlapping.pk]))
        overlapping.refresh_from_db()
        assert overlapping.session_chair_id is None


# ---------------------------------------------------------------------------
# Tight room-transition warning tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTightTransitionWarning:
    """A warning (not error) is shown when the moderator must change rooms quickly."""

    def test_tight_transition_shows_warning(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Chairing a talk right after one in a different room produces a yellow warning."""
        other_room = baker.make(Room, name="Side Hall", event=event)
        start = _morning()
        baker.make(
            Talk,
            title="Earlier Session",
            room=other_room,
            event=event,
            session_chair=moderator,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        # Starts exactly when the other ends - 0-minute gap.
        adjacent = baker.make(
            Talk,
            room=room,
            event=event,
            start_time=start + timedelta(minutes=30),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.post(
            reverse("toggle_session_chair", args=[adjacent.pk]),
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK
        # The talk is assigned (it's a warning, not a blocking error).
        adjacent.refresh_from_db()
        assert adjacent.session_chair_id == moderator.pk
        content = response.content.decode()
        assert "minutes to change rooms" in content
        assert "Earlier Session" in content

    def test_no_warning_for_same_room(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """Back-to-back talks in the same room do not trigger the warning."""
        start = _morning()
        baker.make(
            Talk,
            room=room,
            event=event,
            session_chair=moderator,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        adjacent = baker.make(
            Talk,
            room=room,
            event=event,
            start_time=start + timedelta(minutes=30),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.post(
            reverse("toggle_session_chair", args=[adjacent.pk]),
            HTTP_HX_REQUEST="true",
        )
        adjacent.refresh_from_db()
        assert adjacent.session_chair_id == moderator.pk
        assert b"minutes to change rooms" not in response.content

    def test_no_warning_for_large_gap(
        self,
        client: Client,
        moderator: CustomUser,
        room: Room,
        event: Event,
    ) -> None:
        """A gap larger than 5 minutes between rooms does not trigger the warning."""
        other_room = baker.make(Room, name="Side Hall", event=event)
        start = _morning()
        baker.make(
            Talk,
            room=other_room,
            event=event,
            session_chair=moderator,
            start_time=start,
            duration=timedelta(minutes=30),
        )
        later = baker.make(
            Talk,
            room=room,
            event=event,
            start_time=start + timedelta(minutes=36),
            duration=timedelta(minutes=30),
        )
        client.force_login(moderator)
        response = client.post(
            reverse("toggle_session_chair", args=[later.pk]),
            HTTP_HX_REQUEST="true",
        )
        later.refresh_from_db()
        assert later.session_chair_id == moderator.pk
        assert b"minutes to change rooms" not in response.content


# ---------------------------------------------------------------------------
# Admin assignment tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestAdminAssignment:
    """Admins (superusers) can assign any eligible moderator as chair."""

    def test_admin_assigns_another_moderator(
        self,
        client: Client,
        admin_user: CustomUser,
        other_moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """An admin can assign a different moderator to an unassigned block."""
        client.force_login(admin_user)
        client.post(
            reverse("toggle_session_chair", args=[talk.pk]),
            {"chair_user_id": str(other_moderator.pk)},
        )
        talk.refresh_from_db()
        assert talk.session_chair_id == other_moderator.pk

    def test_admin_clears_any_chair(
        self,
        client: Client,
        admin_user: CustomUser,
        other_moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """An admin can remove a chair set by another moderator."""
        talk.session_chair = other_moderator
        talk.save(update_fields=["session_chair"])
        client.force_login(admin_user)
        client.post(
            reverse("toggle_session_chair", args=[talk.pk]),
            {"chair_user_id": ""},
        )
        talk.refresh_from_db()
        assert talk.session_chair_id is None

    def test_admin_can_reassign_block(
        self,
        client: Client,
        admin_user: CustomUser,
        moderator: CustomUser,
        other_moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """An admin can swap the chair of a block from one moderator to another."""
        talk.session_chair = moderator
        talk.save(update_fields=["session_chair"])
        client.force_login(admin_user)
        client.post(
            reverse("toggle_session_chair", args=[talk.pk]),
            {"chair_user_id": str(other_moderator.pk)},
        )
        talk.refresh_from_db()
        assert talk.session_chair_id == other_moderator.pk

    def test_non_admin_cannot_use_chair_user_id(
        self,
        client: Client,
        moderator: CustomUser,
        other_moderator: CustomUser,
        talk: Talk,
    ) -> None:
        """A regular staff moderator cannot assign another user via chair_user_id."""
        client.force_login(moderator)
        response = client.post(
            reverse("toggle_session_chair", args=[talk.pk]),
            {"chair_user_id": str(other_moderator.pk)},
        )
        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_admin_conflict_blocks_assignment(
        self,
        client: Client,
        admin_user: CustomUser,
        other_moderator: CustomUser,
        event: Event,
        talk: Talk,
    ) -> None:
        """Admin assignment is blocked when the target already chairs an overlapping talk."""
        other_room = baker.make(Room, name="Side Hall", event=event)
        baker.make(
            Talk,
            room=other_room,
            event=event,
            session_chair=other_moderator,
            start_time=talk.start_time,
            duration=timedelta(minutes=30),
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("toggle_session_chair", args=[talk.pk]),
            {"chair_user_id": str(other_moderator.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK
        assert b"already chairs" in response.content
        talk.refresh_from_db()
        assert talk.session_chair_id is None

    def test_admin_sees_available_chairs_in_grid(
        self,
        client: Client,
        admin_user: CustomUser,
        moderator: CustomUser,
    ) -> None:
        """The grid context exposes available_chairs (and is_admin=True) for superusers."""
        client.force_login(admin_user)
        response = client.get(reverse("chair_grid"))
        assert response.status_code == HTTPStatus.OK
        assert response.context["is_admin"] is True
        chairs = response.context["available_chairs"]
        assert any(u.pk == moderator.pk for u in chairs)

    def test_non_admin_has_no_available_chairs(
        self,
        client: Client,
        moderator: CustomUser,
    ) -> None:
        """Regular moderators get is_admin=False and an empty available_chairs list."""
        client.force_login(moderator)
        response = client.get(reverse("chair_grid"))
        assert response.status_code == HTTPStatus.OK
        assert response.context["is_admin"] is False
        assert response.context["available_chairs"] == []
