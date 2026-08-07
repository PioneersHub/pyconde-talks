"""
End-to-end checks on what an anonymous visitor actually receives.

``test_visibility.py`` covers the queryset rules; this file renders real responses, because the
thing that leaks is the video URL in the HTML, not a flag in a context dict. Assertions are
therefore made against the response body.
"""

from datetime import datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test import Client


VIDEO_URL = "https://youtube.com/watch?v=secret-recording"


def _talk_on(
    visibility: str,
    *,
    slug: str,
    title: str = "A talk",
    start_time: datetime | None = None,
) -> Talk:
    """
    Create a talk with a recording on an event with the given visibility.

    The slot defaults to the past on purpose. ``Talk.start_time`` defaults to the ``FAR_FUTURE``
    sentinel, and an upcoming talk withholds its links anyway unless ``SHOW_UPCOMING_TALKS_LINKS``
    is set. Leaving the default would make every "the video is withheld" assertion below pass
    without the video gate doing anything at all.

    Tests for the upcoming-talks fragment pass a future slot instead, since that view only selects
    talks that have not started yet.
    """
    event = Event.objects.create(name=slug, slug=slug, visibility=visibility)
    return baker.make(
        Talk,
        event=event,
        title=title,
        video_link=VIDEO_URL,
        start_time=start_time or (timezone.now() - timedelta(days=1)),
        duration=timedelta(minutes=30),
    )


@pytest.mark.django_db
class TestAnonymousTalkList:
    """The talk list is open, and scoped per row."""

    def test_renders_without_a_login(self, client: Client) -> None:
        """The page itself no longer redirects."""
        response = client.get(reverse("talk_list"))
        assert response.status_code == HTTPStatus.OK

    def test_hidden_event_talks_are_absent(self, client: Client) -> None:
        """A hidden event contributes nothing to the anonymous list."""
        talk = _talk_on(Event.Visibility.HIDDEN, slug="hidden", title="Secret Talk")
        response = client.get(reverse("talk_list"), {"event": "all"})
        assert talk.title.encode() not in response.content

    def test_public_event_talks_are_listed(self, client: Client) -> None:
        """A public event is browsable without an account."""
        talk = _talk_on(Event.Visibility.PUBLIC, slug="public", title="Open Talk")
        response = client.get(reverse("talk_list"), {"event": "all"})
        assert talk.title.encode() in response.content

    def test_event_query_param_cannot_reach_a_hidden_event(self, client: Client) -> None:
        """
        ``?event=<id>`` narrows the visible set, it does not widen it.

        The visibility filter is applied to the base queryset, so naming a hidden event explicitly
        returns nothing rather than bypassing the scoping.
        """
        hidden = _talk_on(Event.Visibility.HIDDEN, slug="hidden", title="Secret Talk")
        response = client.get(reverse("talk_list"), {"event": str(hidden.event_id)})
        assert response.status_code == HTTPStatus.OK
        assert hidden.title.encode() not in response.content

    def test_saved_filter_does_not_error(self, client: Client) -> None:
        """``?saved=1`` used to raise TypeError for anonymous visitors."""
        _talk_on(Event.Visibility.PUBLIC, slug="public")
        response = client.get(reverse("talk_list"), {"saved": "1"})
        assert response.status_code == HTTPStatus.OK


@pytest.mark.django_db
class TestAnonymousTalkDetail:
    """Detail pages are open, but recordings are not."""

    def test_schedule_only_shows_the_talk_but_withholds_the_video(
        self,
        client: Client,
    ) -> None:
        """The whole point of the middle visibility state."""
        talk = _talk_on(Event.Visibility.SCHEDULE_ONLY, slug="schedule-only", title="Programme")
        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))
        body = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert talk.title in body
        # Assert on the URL itself: that is what would leak, whatever the template does.
        assert VIDEO_URL not in body
        assert "<iframe" not in body

    def test_public_event_serves_the_video(self, client: Client) -> None:
        """Once an event is public the recording plays without an account."""
        talk = _talk_on(Event.Visibility.PUBLIC, slug="public", title="Open Talk")
        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))
        assert VIDEO_URL in response.content.decode()

    def test_hidden_event_redirects_anonymous_to_login(self, client: Client) -> None:
        """
        A logged-out visitor is asked to sign in rather than told the talk does not exist.

        They may well hold a ticket; they just have no session yet.
        """
        talk = _talk_on(Event.Visibility.HIDDEN, slug="hidden")
        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))
        assert response.status_code == HTTPStatus.FOUND
        assert "/accounts/login/" in response.headers.get("Location", "")

    def test_hidden_event_is_404_for_a_logged_in_outsider(self, client: Client) -> None:
        """
        Someone already logged in gets a 404, which does not confirm the talk exists.

        The redirect above is only useful to a visitor who has not signed in yet; reusing it here
        would tell any account holder that a given talk id is real.
        """
        talk = _talk_on(Event.Visibility.HIDDEN, slug="hidden")
        outsider = baker.make(CustomUser, email="outsider@example.com")
        client.force_login(outsider)
        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_member_sees_the_video_on_a_schedule_only_event(self, client: Client) -> None:
        """A ticket holder still watches recordings on a schedule-only event."""
        talk = _talk_on(Event.Visibility.SCHEDULE_ONLY, slug="schedule-only")
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(talk.event)
        client.force_login(member)

        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))
        assert VIDEO_URL in response.content.decode()

    def test_anonymous_page_hides_the_account_only_controls(self, client: Client) -> None:
        """Q&A, rating and save all need an account, so none of them are offered."""
        talk = _talk_on(Event.Visibility.PUBLIC, slug="public")
        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))
        body = response.content.decode()

        assert reverse("toggle_save_talk", kwargs={"talk_id": talk.pk}) not in body
        assert reverse("rate_talk", kwargs={"talk_id": talk.pk}) not in body


@pytest.mark.django_db
class TestAnonymousSchedule:
    """The schedule grid follows the same scoping as the list."""

    def test_hidden_event_is_absent(self, client: Client) -> None:
        """A hidden event's talks do not appear in the anonymous grid."""
        talk = _talk_on(Event.Visibility.HIDDEN, slug="hidden", title="Secret Talk")
        response = client.get(
            reverse("schedule"),
            {"date": talk.start_time.date().isoformat(), "event": str(talk.event_id)},
        )
        assert response.status_code == HTTPStatus.OK
        assert talk.title.encode() not in response.content


@pytest.mark.django_db
class TestAnonymousDashboardStats:
    """The stats widget is public, and counts only what the viewer could reach."""

    def test_counts_only_publicly_listed_events(self, client: Client) -> None:
        """A hidden event contributes nothing to the anonymous totals."""
        _talk_on(Event.Visibility.HIDDEN, slug="hidden", title="Secret Talk")
        public = _talk_on(Event.Visibility.PUBLIC, slug="public", title="Open Talk")

        response = client.get(reverse("dashboard_stats"))
        body = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert public.event.name in body
        assert "hidden" not in body

    def test_hidden_talks_are_not_counted(self, client: Client) -> None:
        """
        ``hide`` keeps a talk out of the totals as well as out of the list.

        The counts sit next to the list they describe, so counting a talk the visitor cannot see
        would make the two disagree.
        """
        talk = _talk_on(Event.Visibility.PUBLIC, slug="public", title="Shown")
        baker.make(Talk, event=talk.event, title="Embargoed", hide=True)

        response = client.get(reverse("dashboard_stats"))
        body = response.content.decode()

        # The widget renders each figure as a styled div, so match the total precisely:
        # a loose ">1<" would also match unrelated markup and pass either way.
        total_markup = '<div class="text-xl sm:text-2xl font-bold tabular-nums">{}</div>'
        assert total_markup.format(1) in body
        assert total_markup.format(2) not in body

    def test_member_and_anonymous_totals_differ(self, client: Client) -> None:
        """
        The response is not shared between viewers.

        It used to be cached on the cookie header, which cannot distinguish two anonymous visitors
        from each other or from a member whose cookies happen to match.
        """
        talk = _talk_on(Event.Visibility.HIDDEN, slug="hidden", title="Members Only")
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(talk.event)

        anonymous_body = client.get(reverse("dashboard_stats")).content.decode()
        assert talk.event.name not in anonymous_body

        client.force_login(member)
        member_body = client.get(reverse("dashboard_stats")).content.decode()
        assert talk.event.name in member_body


@pytest.mark.django_db
class TestAnonymousUpcomingTalks:
    """The upcoming-talks fragment is open and no longer cached."""

    def test_renders_and_scopes_by_visibility(self, client: Client) -> None:
        """Only non-hidden events reach an anonymous visitor."""
        hidden = _talk_on(
            Event.Visibility.HIDDEN,
            slug="hidden",
            title="Secret Talk",
            start_time=timezone.now() + timedelta(hours=1),
        )
        response = client.get(reverse("upcoming_talks"))

        assert response.status_code == HTTPStatus.OK
        assert hidden.title.encode() not in response.content

    def test_member_and_anonymous_do_not_share_a_cached_fragment(
        self,
        client: Client,
    ) -> None:
        """
        The fragment used to be cached on the cookie header.

        That was safe only while every visitor was authenticated with a distinct session cookie. Two
        back-to-back requests must reflect their own viewer, not the first one.
        """
        talk = _talk_on(
            Event.Visibility.HIDDEN,
            slug="hidden",
            title="Members Only Talk",
            start_time=timezone.now() + timedelta(hours=1),
        )
        member = baker.make(CustomUser, email="member@example.com")
        member.events.add(talk.event)

        anonymous_body = client.get(reverse("upcoming_talks")).content
        assert talk.title.encode() not in anonymous_body

        client.force_login(member)
        member_body = client.get(reverse("upcoming_talks")).content
        assert talk.title.encode() in member_body
