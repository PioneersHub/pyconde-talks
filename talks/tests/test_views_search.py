"""Tests for TalkListView search filter."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.models import Rating, Speaker, Talk
from talks.views import _apply_search_filter
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


# Expected aggregates for the search fan-out regression test below.
_EXPECTED_RATING_COUNT = 2
_EXPECTED_AVERAGE = 4.0


@pytest.fixture()
def event() -> Event:
    """Return the event the searched talks and logged-in user share (event-scoped)."""
    return Event.objects.create(slug="search", name="Search", year=2099)


@pytest.mark.django_db
class TestTalkListSearch:
    """Verify full-text search by title, description, abstract and speaker name."""

    @pytest.fixture(autouse=True)
    def _login(self, client: Client, event: Event) -> None:
        """Create and log in a user with access to the search event."""
        user = CustomUser.objects.create(email="m.palin@example.com")
        user.events.add(event)
        client.force_login(user)

    def test_search_by_title(self, client: Client, event: Event) -> None:
        """A query should match titles case-insensitively."""
        baker.make(Talk, event=event, title="Spam 101: Nudge Nudge")
        baker.make(Talk, event=event, title="Nobody expects unladen swallows")

        url = reverse("talk_list") + "?q=spam"
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        content = resp.content.decode()
        # Highlighting wraps matches with mark tags; strip them for assertion
        content_no_mark = re.sub(r"</?mark[^>]*>", "", content, flags=re.IGNORECASE)
        assert "Spam 101" in content_no_mark
        assert "swallows" not in content

    def test_search_by_description_and_abstract(self, client: Client, event: Event) -> None:
        """Query should match both description and abstract fields."""
        t1 = baker.make(
            Talk,
            event=event,
            title="DescMatch The Olympic Hide-and-Seek Final",
            description=("Deep dive into how not to be seen by a parrot."),
        )
        t2 = baker.make(
            Talk,
            event=event,
            title="AbsMatch The Lumberjack has ceased to be",
            abstract=("According to the parrot: tis but a scratch."),
        )
        t3 = baker.make(
            Talk,
            event=event,
            title="NoMatch Cheese Shop",
            description="Something completely different about coconuts",
        )

        url = reverse("talk_list") + "?q=parrot"
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        content = resp.content.decode()
        assert t1.title in content
        assert t2.title in content
        assert t3.title not in content

    def test_search_by_speaker_name(self, client: Client, event: Event) -> None:
        """Query should match talks having speakers with matching names."""
        # Create two talks with different speakers
        talk1 = baker.make(Talk, event=event, title="Introduction to Silly Walks a1b2")
        talk2 = baker.make(Talk, event=event, title="Self-Defence Against Fresh Fruit c3d4")
        speaker1: Speaker = baker.make(Speaker, name="Graham Chapman")
        speaker2: Speaker = baker.make(Speaker, name="John Cleese")
        talk1.speakers.add(speaker1)
        talk2.speakers.add(speaker2)

        url = reverse("talk_list") + "?q=chapman&search_in=author"
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        content = resp.content.decode()
        assert talk1.title in content
        assert talk2.title not in content

    def test_search_empty_returns_all(self, client: Client, event: Event) -> None:
        """Empty query value should behave like no search param (no filter applied)."""
        baker.make(Talk, event=event, _quantity=3)

        url = reverse("talk_list") + "?q="
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        assert "No talks found" not in resp.content.decode()

    def test_search_scope_title_only(self, client: Client, event: Event) -> None:
        """When limited to title, matches in author should be ignored."""
        # Title contains the token only for talk_a
        baker.make(Talk, event=event, title="Brian and the Holy Grail")
        talk_b = baker.make(Talk, event=event, title="The Bright Side of Life")
        # Speaker name contains token for talk B, but should be ignored with title scope
        sp: Speaker = baker.make(Speaker, name="Brian Cohen")
        talk_b.speakers.add(sp)

        url = reverse("talk_list") + "?q=brian&search_in=title"
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        content = resp.content.decode().lower()
        # Check words from the matching title appear (allowing for highlight markup)
        assert "holy" in content
        assert "grail" in content
        # Non-matching title should not appear
        assert talk_b.title.lower() not in content


@pytest.mark.django_db
def test_rating_count_not_inflated_by_search_speaker_join() -> None:
    """
    A multi-speaker talk reports its true rating count when a search joins speakers.

    The default ("all") search scope ORs a Q(speakers__name__icontains=...) into the filter,
    which fans each talk row out once per speaker. Before the fix, the shared with_rating_stats()
    annotation used a plain Count("ratings"), so the displayed count was multiplied by the
    speaker count. This drives the exact query chain TalkListView.get_queryset builds.
    """
    event = Event.objects.create(slug="ratings", name="Ratings", year=2099)
    talk = baker.make(Talk, event=event, title="Distinctive Title")
    talk.speakers.add(baker.make(Speaker), baker.make(Speaker))  # two speakers -> 2x fan-out
    for _ in range(_EXPECTED_RATING_COUNT):
        Rating.objects.create(talk=talk, user=baker.make(CustomUser), score=4)

    # Matches by title; the OR still joins all speakers, reproducing the fan-out.
    request = RequestFactory().get("/", {"q": "Distinctive", "search_in": "all"})
    stats = (
        _apply_search_filter(Talk.objects.all(), request)
        .with_rating_stats()
        .values("rating_count", "average_rating")
        .get(pk=talk.pk)
    )

    assert stats["rating_count"] == _EXPECTED_RATING_COUNT  # not 4
    assert stats["average_rating"] == _EXPECTED_AVERAGE
