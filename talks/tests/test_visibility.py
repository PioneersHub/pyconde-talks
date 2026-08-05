"""
Tests for event-visibility access rules at the queryset level.

``TalkQuerySet.accessible_to`` is the one gate every talk-facing view goes through, so these
tests are written as an exhaustive truth table over (viewer kind) x (event visibility) rather
than as a handful of examples. A leak here is a leak everywhere.
"""

from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from events.session import events_visible_to
from talks.models import Talk, unlock_video_access, user_can_watch_videos
from users.models import CustomUser
from utils.test_perf import assert_no_n_plus_one


if TYPE_CHECKING:
    from django.test import Client


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


VIDEO_URL = "https://youtube.com/watch?v=abc"


def _talk_for(event: Event) -> Talk:
    """Create a talk belonging to *event*."""
    return baker.make(Talk, event=event, title=f"Talk for {event.slug}")


def _recorded_talk_for(event: Event) -> Talk:
    """
    Create a talk on *event* with a recording and a slot that has already happened.

    The past slot is the point. ``Talk.start_time`` defaults to the ``FAR_FUTURE`` sentinel, and
    an upcoming talk withholds its links whatever the video gate says, so a talk left at the
    default would let these assertions pass without the gate being exercised at all.
    """
    return baker.make(
        Talk,
        event=event,
        video_link=VIDEO_URL,
        start_time=timezone.now() - timedelta(days=1),
        duration=timedelta(minutes=30),
    )


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
class TestVideoGate:
    """Recording access is decided separately from listing access, and fails closed."""

    @pytest.mark.parametrize(
        ("visibility", "expected"),
        [
            (Event.Visibility.HIDDEN, False),
            (Event.Visibility.SCHEDULE_ONLY, False),
            (Event.Visibility.PUBLIC, True),
        ],
    )
    def test_anonymous_only_watches_public_events(
        self,
        visibility: str,
        expected: bool,  # noqa: FBT001
    ) -> None:
        """A schedule-only event lists its talks publicly but keeps the recordings back."""
        event = baker.make(Event, visibility=visibility)
        assert user_can_watch_videos(AnonymousUser(), event) is expected

    def test_members_watch_their_own_events(self, hidden_event: Event) -> None:
        """A ticket holder watches recordings whatever the event's visibility."""
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(hidden_event)
        assert user_can_watch_videos(member, hidden_event) is True

    def test_outsiders_cannot_watch_a_schedule_only_event(
        self,
        schedule_only_event: Event,
    ) -> None:
        """Being logged in without a ticket is no better than being anonymous."""
        outsider = baker.make(CustomUser, email="outsider@example.com")
        assert user_can_watch_videos(outsider, schedule_only_event) is False

    def test_superusers_always_watch(self, hidden_event: Event) -> None:
        """Superusers bypass the video gate."""
        superuser = CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password",
        )
        assert user_can_watch_videos(superuser, hidden_event) is True

    def test_missing_event_denies(self) -> None:
        """Without an event there is no visibility to check, so deny."""
        assert user_can_watch_videos(AnonymousUser(), None) is False

    def test_link_is_withheld_until_a_view_unlocks_it(self, public_event: Event) -> None:
        """
        The gate fails closed.

        A talk that no view has run through ``allow_videos_for`` or ``unlock_video_access``
        yields no link even on a public event. A view that forgets the call renders a missing
        player, which is obvious, instead of leaking a recording, which is not.
        """
        talk = _recorded_talk_for(public_event)
        assert talk.get_video_link() == ""
        assert talk.allow_videos_for(AnonymousUser()).get_video_link() != ""

    def test_transcription_follows_the_same_gate(self, schedule_only_event: Event) -> None:
        """A transcription is the recording in text form, so it is withheld alongside it."""
        talk = baker.make(
            Talk,
            event=schedule_only_event,
            transcription_url="https://example.com/transcript",
        )
        assert talk.allow_videos_for(AnonymousUser()).get_transcription_url() == ""

        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(schedule_only_event)
        assert talk.allow_videos_for(member).get_transcription_url() != ""

    def test_has_recording_ignores_the_viewer(self, schedule_only_event: Event) -> None:
        """
        The catalogue view of "is there a recording" must not move with the viewer.

        The dashboard counter and the admin column report on the data, not on a player, so
        they would otherwise show different totals to different people.
        """
        talk = _recorded_talk_for(schedule_only_event)
        talk.allow_videos_for(AnonymousUser())
        assert talk.get_video_link() == ""
        assert talk.has_recording() is True

    def test_unlock_video_access_is_not_n_plus_one(self, public_event: Event) -> None:
        """Deciding the gate for many rows costs one membership query, not one per row."""
        talks = [baker.make(Talk, event=public_event) for _ in range(5)]
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(public_event)

        loaded = list(Talk.objects.select_related("event").filter(event=public_event))
        with assert_no_n_plus_one():
            unlock_video_access(loaded, member)

        assert all(talk.videos_unlocked for talk in loaded)
        assert len(loaded) == len(talks)

    def test_unlock_video_access_handles_an_empty_list(self) -> None:
        """No rows means no queries and no error."""
        unlock_video_access([], AnonymousUser())


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


@pytest.mark.django_db
class TestAccessibleToIsActive:
    """Deactivating an event takes it off the site for visitors who are not members."""

    def test_anonymous_cannot_list_talks_of_a_deactivated_public_event(self) -> None:
        """
        ``is_active`` is how an organizer pulls an event, so it has to bind the public half.

        ``events_visible_to`` already drops inactive events from the picker; without the same
        filter here the talks stayed reachable at their direct URLs, recording included.
        """
        event = Event.objects.create(
            name="Retired",
            slug="retired",
            visibility=Event.Visibility.PUBLIC,
            is_active=False,
        )
        talk = _talk_for(event)

        assert talk not in Talk.objects.accessible_to(AnonymousUser())
        assert list(events_visible_to(AnonymousUser())) == []

    def test_the_detail_page_is_gone_for_anonymous_visitors(self, client: Client) -> None:
        """End to end: no 200, and no recording in the body."""
        event = Event.objects.create(
            name="Retired",
            slug="retired",
            visibility=Event.Visibility.PUBLIC,
            is_active=False,
        )
        talk = _recorded_talk_for(event)

        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))

        # Anonymous visitors are sent to log in rather than shown a bare 404.
        assert response.status_code == HTTPStatus.FOUND
        assert VIDEO_URL.encode() not in response.content

    def test_members_lose_a_deactivated_event_too(self) -> None:
        """
        Deactivating an event hides it from everyone, ticket holders included.

        The event is already gone from their picker, so anything still reachable would only be
        reachable by direct URL. Holding a ticket does not make a withdrawn event visible again.
        """
        event = Event.objects.create(
            name="Retired",
            slug="retired",
            visibility=Event.Visibility.PUBLIC,
            is_active=False,
        )
        talk = _talk_for(event)
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(event)

        assert talk not in Talk.objects.accessible_to(member)
        assert user_can_watch_videos(member, event) is False

    def test_a_member_of_a_deactivated_hidden_event_sees_nothing(self) -> None:
        """The same for a hidden event, which is the usual state of a finished conference."""
        event = Event.objects.create(
            name="Last year",
            slug="last-year",
            visibility=Event.Visibility.HIDDEN,
            is_active=False,
        )
        talk = _talk_for(event)
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(event)

        assert talk not in Talk.objects.accessible_to(member)

    def test_superusers_still_see_a_deactivated_event(self) -> None:
        """Administrators keep their view of everything, which is how it gets fixed."""
        event = Event.objects.create(
            name="Retired",
            slug="retired",
            visibility=Event.Visibility.PUBLIC,
            is_active=False,
        )
        talk = _talk_for(event)
        superuser = CustomUser.objects.create_superuser(
            email="admin@example.com",
            password="password",
        )

        assert talk in Talk.objects.accessible_to(superuser)

    def test_an_active_public_event_is_unaffected(self) -> None:
        """The ordinary case must keep working."""
        event = Event.objects.create(
            name="Live",
            slug="live",
            visibility=Event.Visibility.PUBLIC,
            is_active=True,
        )
        talk = _talk_for(event)

        assert talk in Talk.objects.accessible_to(AnonymousUser())

    def test_the_video_gate_closes_with_the_event(self, client: Client) -> None:
        """
        ``user_can_watch_videos`` states the same rule as ``accessible_to`` and must agree.

        Two spellings of one rule that drift apart is how a gate ends up open on one path.
        """
        event = Event.objects.create(
            name="Retired",
            slug="retired",
            visibility=Event.Visibility.PUBLIC,
            is_active=False,
        )

        assert user_can_watch_videos(AnonymousUser(), event) is False
