"""
Tests for event-visibility access rules at the queryset level.

``TalkQuerySet.accessible_to`` is the one gate every talk-facing view goes through, so these
tests are written as an exhaustive truth table over (viewer kind) x (event visibility) rather
than as a handful of examples. A leak here is a leak everywhere.
"""

import pytest
from django.contrib.auth.models import AnonymousUser
from model_bakery import baker

from events.models import Event
from events.session import events_visible_to
from talks.models import Talk
from users.models import CustomUser


@pytest.fixture
def hidden_event() -> Event:
    """Return an event that is entirely behind the login wall."""
    return Event.objects.create(
        name="Hidden Event",
        slug="hidden-event",
        visibility=Event.Visibility.HIDDEN,
    )


@pytest.fixture
def schedule_only_event() -> Event:
    """Return an event whose programme is public but whose recordings are not."""
    return Event.objects.create(
        name="Schedule Only Event",
        slug="schedule-only-event",
        visibility=Event.Visibility.SCHEDULE_ONLY,
    )


@pytest.fixture
def public_event() -> Event:
    """Return a fully public event."""
    return Event.objects.create(
        name="Public Event",
        slug="public-event",
        visibility=Event.Visibility.PUBLIC,
    )


def _talk_for(event: Event) -> Talk:
    """Create a talk belonging to *event*."""
    return baker.make(Talk, event=event, title=f"Talk for {event.slug}")


@pytest.mark.django_db
class TestAccessibleToVisibility:
    """The (viewer, visibility) truth table for listing access."""

    @pytest.mark.parametrize(
        ("visibility", "expected"),
        [
            (Event.Visibility.HIDDEN, False),
            (Event.Visibility.SCHEDULE_ONLY, True),
            (Event.Visibility.PUBLIC, True),
        ],
    )
    def test_anonymous_sees_only_non_hidden_events(
        self,
        visibility: str,
        expected: bool,  # noqa: FBT001
    ) -> None:
        """An anonymous visitor sees non-hidden events and nothing else."""
        event = baker.make(Event, visibility=visibility)
        talk = _talk_for(event)
        visible = Talk.objects.accessible_to(AnonymousUser())
        assert (talk in visible) is expected

    def test_anonymous_does_not_raise(self, hidden_event: Event) -> None:
        """
        AnonymousUser has no ``events`` relation.

        Dereferencing it used to raise AttributeError, which is a 500 rather than an empty
        list, so this asserts the queryset evaluates at all.
        """
        _talk_for(hidden_event)
        assert list(Talk.objects.accessible_to(AnonymousUser())) == []

    def test_none_is_treated_as_anonymous(self, public_event: Event) -> None:
        """Passing ``None`` resolves to the public set rather than blowing up."""
        talk = _talk_for(public_event)
        assert list(Talk.objects.accessible_to(None)) == [talk]

    def test_member_sees_their_hidden_event(self, hidden_event: Event) -> None:
        """A ticket holder still sees a hidden event, which is the pre-existing behaviour."""
        talk = _talk_for(hidden_event)
        user = baker.make(CustomUser, email="member@example.com")
        user.events.add(hidden_event)
        assert list(Talk.objects.accessible_to(user)) == [talk]

    def test_outsider_cannot_see_a_hidden_event(self, hidden_event: Event) -> None:
        """
        Being logged in is not itself access.

        A user with no ticket for a hidden event must see exactly what an anonymous visitor
        sees. This is the case a naive rewrite breaks.
        """
        _talk_for(hidden_event)
        outsider = baker.make(CustomUser, email="outsider@example.com")
        assert list(Talk.objects.accessible_to(outsider)) == []

    def test_outsider_sees_a_public_event(self, public_event: Event) -> None:
        """A logged-in visitor may browse a public event they hold no ticket for."""
        talk = _talk_for(public_event)
        outsider = baker.make(CustomUser, email="outsider@example.com")
        assert list(Talk.objects.accessible_to(outsider)) == [talk]

    def test_member_sees_the_union_of_their_events_and_public_ones(
        self,
        hidden_event: Event,
        public_event: Event,
    ) -> None:
        """Membership adds to the public set rather than replacing it."""
        own = _talk_for(hidden_event)
        public = _talk_for(public_event)
        other_hidden = _talk_for(baker.make(Event, visibility=Event.Visibility.HIDDEN))

        user = baker.make(CustomUser, email="member@example.com")
        user.events.add(hidden_event)

        visible = set(Talk.objects.accessible_to(user))
        assert visible == {own, public}
        assert other_hidden not in visible

    @pytest.mark.parametrize(
        "visibility",
        [Event.Visibility.HIDDEN, Event.Visibility.SCHEDULE_ONLY, Event.Visibility.PUBLIC],
    )
    def test_superuser_sees_everything(self, visibility: str) -> None:
        """Superusers bypass visibility entirely."""
        talk = _talk_for(baker.make(Event, visibility=visibility))
        superuser = CustomUser.objects.create_superuser(
            email=f"admin-{visibility}@example.com",
            password="password",
        )
        assert talk in Talk.objects.accessible_to(superuser)


@pytest.mark.django_db
class TestAccessibleToHide:
    """``Talk.hide`` withholds a single talk regardless of its event's visibility."""

    @pytest.mark.parametrize(
        "visibility",
        [Event.Visibility.HIDDEN, Event.Visibility.SCHEDULE_ONLY, Event.Visibility.PUBLIC],
    )
    def test_hidden_talk_is_withheld_at_every_visibility(self, visibility: str) -> None:
        """Opening an event up does not publish a talk that was explicitly held back."""
        event = baker.make(Event, visibility=visibility)
        hidden_talk = baker.make(Talk, event=event, title="Embargoed", hide=True)
        shown_talk = baker.make(Talk, event=event, title="Announced", hide=False)

        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(event)

        # The member can reach the event whatever its visibility, so they are the strongest
        # non-superuser case: the announced talk is there, the embargoed one is not.
        member_visible = set(Talk.objects.accessible_to(member))
        assert shown_talk in member_visible
        assert hidden_talk not in member_visible

        assert hidden_talk not in set(Talk.objects.accessible_to(AnonymousUser()))

    def test_ticket_holders_do_not_see_hidden_talks(self, public_event: Event) -> None:
        """
        ``hide`` outranks membership.

        The field is for embargoed or cancelled sessions, so holding a ticket is not enough.
        """
        talk = baker.make(Talk, event=public_event, hide=True)
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(public_event)
        assert talk not in Talk.objects.accessible_to(member)

    def test_superusers_still_see_hidden_talks(self, public_event: Event) -> None:
        """Administrators need to find a hidden talk in order to unhide it."""
        talk = baker.make(Talk, event=public_event, hide=True)
        superuser = CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password",
        )
        assert talk in Talk.objects.accessible_to(superuser)


@pytest.mark.django_db
class TestEventsVisibleTo:
    """``visible_events`` and ``events_visible_to`` must agree with ``accessible_to``."""

    def test_anonymous_gets_non_hidden_active_events(
        self,
        hidden_event: Event,
        schedule_only_event: Event,
        public_event: Event,
    ) -> None:
        """The event picker offers anonymous visitors exactly the browsable events."""
        assert set(events_visible_to(AnonymousUser())) == {schedule_only_event, public_event}
        assert hidden_event not in events_visible_to(AnonymousUser())

    def test_outsider_matches_anonymous_plus_membership(
        self,
        hidden_event: Event,
        public_event: Event,
    ) -> None:
        """A user sees public events plus the ones they hold a ticket for."""
        user = baker.make(CustomUser, email="member@example.com")
        user.events.add(hidden_event)
        assert set(events_visible_to(user)) == {hidden_event, public_event}

    def test_inactive_events_are_always_excluded(self, public_event: Event) -> None:
        """
        ``is_active`` still wins over visibility.

        A public but deactivated event must stay off the site, otherwise turning an event off
        would be silently undone by making it public.
        """
        public_event.is_active = False
        public_event.save(update_fields=["is_active"])
        assert list(events_visible_to(AnonymousUser())) == []

        user = baker.make(CustomUser, email="member@example.com")
        assert list(events_visible_to(user)) == []

    def test_superuser_sees_hidden_events(self, hidden_event: Event) -> None:
        """Superusers get every active event regardless of visibility."""
        superuser = CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password",
        )
        assert hidden_event in events_visible_to(superuser)

    @pytest.mark.parametrize(
        "user",
        [
            pytest.param(AnonymousUser(), id="anonymous"),
            pytest.param(None, id="none"),
        ],
    )
    def test_accepts_users_without_the_relation(
        self,
        user: AnonymousUser | None,
        public_event: Event,
    ) -> None:
        """Neither AnonymousUser nor None has ``visible_events``; both resolve to the public set."""
        assert list(events_visible_to(user)) == [public_event]
