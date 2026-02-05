"""Tests for TalkListView search filter."""

import re
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from model_bakery import baker

from talks.models import Talk


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.mark.django_db
class TestTalkListSearch:
    """Verify full-text search by title, description, abstract and speaker name."""

    @pytest.fixture(autouse=True)
    def _login(self, client: Client) -> None:
        """Create and log in a user for all tests in this class."""
        user_model = get_user_model()
        user = user_model.objects.create(email="m.palin@example.com")
        client.force_login(user)

    def test_search_by_title(self, client: Client) -> None:
        """A query should match titles case-insensitively."""
        baker.make(Talk, title="Spam 101: Nudge Nudge")
        baker.make(Talk, title="Nobody expects unladen swallows")

        url = reverse("talk_list") + "?q=spam"
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        content = resp.content.decode()
        # Highlighting wraps matches with mark tags; strip them for assertion
        content_no_mark = re.sub(r"</?mark[^>]*>", "", content, flags=re.IGNORECASE)
        assert "Spam 101" in content_no_mark
        assert "swallows" not in content

    def test_search_by_description_and_abstract(self, client: Client) -> None:
        """Query should match both description and abstract fields."""
        t1 = baker.make(
            Talk,
            title="DescMatch The Olympic Hide-and-Seek Final",
            description=("Deep dive into how not to be seen by a parrot."),
        )
        t2 = baker.make(
            Talk,
            title="AbsMatch The Lumberjack has ceased to be",
            abstract=("According to the parrot: tis but a scratch."),
        )
        t3 = baker.make(
            Talk,
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

    def test_search_by_speaker_name(self, client: Client) -> None:
        """Query should match talks having speakers with matching names."""
        # Create two talks with different speakers
        talk1 = baker.make(Talk, title="Introduction to Silly Walks a1b2")
        talk2 = baker.make(Talk, title="Self-Defence Against Fresh Fruit c3d4")
        speaker1 = baker.make("talks.Speaker", name="Graham Chapman")
        speaker2 = baker.make("talks.Speaker", name="John Cleese")
        talk1.speakers.add(speaker1)
        talk2.speakers.add(speaker2)

        url = reverse("talk_list") + "?q=chapman&search_in=author"
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        content = resp.content.decode()
        assert talk1.title in content
        assert talk2.title not in content

    def test_search_empty_returns_all(self, client: Client) -> None:
        """Empty query value should behave like no search param (no filter applied)."""
        baker.make(Talk, _quantity=3)

        url = reverse("talk_list") + "?q="
        resp = client.get(url)
        assert resp.status_code == HTTPStatus.OK
        assert "No talks found" not in resp.content.decode()

    def test_search_scope_title_only(self, client: Client) -> None:
        """When limited to title, matches in author should be ignored."""
        # Title contains the token only for talk_a
        talk_a = baker.make(Talk, title="Brian and the Holy Grail")  # noqa: F841
        talk_b = baker.make(Talk, title="The Bright Side of Life")
        # Speaker name contains token for talk B, but should be ignored with title scope
        sp = baker.make("talks.Speaker", name="Brian Cohen")
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
