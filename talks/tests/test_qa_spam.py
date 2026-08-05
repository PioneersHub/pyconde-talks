"""
Tests for the Q&A spam heuristics.

Most of the value here is in the negative cases. The rules are cheap to make stricter and
expensive to get wrong: a question wrongly held for review annoys one attendee, but a rule that
fires on ordinary questions fills the queue with noise until moderators stop reading it. The
"should not flag" list is therefore written out as real questions people ask at a Python
conference.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from hypothesis import (
    given,
    strategies as st,
)
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from talks.models_qa import Question
from talks.spam import spam_flag_reason
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test import Client
    from pytest_django.fixtures import SettingsWrapper


# Deliberate spam samples and a deliberate typo, not real words.
# cspell:ignore somechannel FREEMONEYNOW spammy slids


SHOULD_FLAG = [
    pytest.param("Cheap tickets at buy-now.com and also deals.xyz", "many_links", id="two-hosts"),
    pytest.param(
        "Great offer https://spam.example.com/a http://spam.example.com/b",
        "many_links",
        id="two-urls",
    ),
    pytest.param("Message me on WhatsApp for the recording", "contact_handle", id="whatsapp"),
    pytest.param("Join t.me/somechannel for more", "contact_handle", id="telegram-link"),
    pytest.param("Follow bit.ly/xyz now", "contact_handle", id="shortener"),
    pytest.param(
        "FREEMONEYNOW visit https://scam.example.com",
        "link_and_shouting",
        id="link-plus-shouting",
    ),
]

SHOULD_NOT_FLAG = [
    pytest.param("How does this compare to https://scikit-learn.org?", id="one-citation-link"),
    pytest.param("Is this like the approach in PEP 8?", id="pep-reference"),
    pytest.param("What about NumPy 2.0 and the new ABI?", id="version-number"),
    pytest.param("I use GPU, SQL and the ORM API daily. Any tips?", id="several-acronyms"),
    pytest.param("Could you share the slides?", id="plain-question"),
    pytest.param("", id="empty"),
    pytest.param("Why Django 5.0 over FastAPI for this?", id="framework-names"),
    pytest.param(
        "You mentioned example.com as a placeholder - was that deliberate?",
        id="single-bare-host",
    ),
]


@pytest.mark.parametrize(("content", "expected"), SHOULD_FLAG)
def test_spam_is_flagged(content: str, expected: str) -> None:
    """Patterns that essentially never appear in a real question are held for review."""
    assert spam_flag_reason(content) == expected


@pytest.mark.parametrize("content", SHOULD_NOT_FLAG)
def test_ordinary_questions_are_not_flagged(content: str) -> None:
    """
    Real questions pass untouched.

    A single link is a citation, not an advert, and a conference audience writes in acronyms.
    """
    assert spam_flag_reason(content) == ""


def test_a_long_link_free_question_is_not_flagged() -> None:
    """Length alone means nothing: thoughtful questions can be long."""
    content = "Why does this approach work so well in practice? " * 40
    assert spam_flag_reason(content) == ""


@given(st.text(alphabet=st.characters(blacklist_characters=".:@/"), max_size=200))
def test_text_without_link_punctuation_is_never_flagged(content: str) -> None:
    """
    A property check on regex over-reach.

    Without a dot, colon, at-sign or slash there is no host, URL, handle or shortener to find,
    so nothing here should ever trip a rule. Guards against a future pattern that is too eager.
    """
    assert spam_flag_reason(content) == ""


def test_configured_keywords_flag(settings: SettingsWrapper) -> None:
    """The operator lever works, for a conference under a specific ongoing campaign."""
    settings.QA_SPAM_KEYWORDS = ["miracle cure"]
    assert spam_flag_reason("Try this MIRACLE CURE today") == "keyword"


def test_no_keywords_are_configured_by_default() -> None:
    """Keyword lists date badly, so the default list is empty."""
    assert spam_flag_reason("Try this miracle cure today") == ""


@pytest.fixture
def talk() -> Talk:
    """Return a talk on an event with an open Q&A."""
    event = Event.objects.create(name="Event", slug="event", qa_mode=Event.QAMode.OPEN)
    return baker.make(Talk, event=event, title="A talk")


@pytest.fixture
def asker(talk: Talk) -> CustomUser:
    """Return a user with access to the talk's event."""
    user = baker.make(CustomUser, email="asker@example.com")
    user.events.add(talk.event)
    return user


@pytest.mark.django_db
class TestSpamHeuristicsInTheViews:
    """The heuristics apply on the way in, and again on edit."""

    def test_a_spammy_question_is_held_even_on_an_open_qa(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
    ) -> None:
        """Open means "no queue by default", not "publish anything"."""
        client.force_login(asker)
        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Deals at buy-now.com and more at deals.xyz"},
        )

        question = Question.objects.get(talk=talk)
        assert question.status == Question.Status.PENDING
        assert question.flag_reason == "many_links"

    def test_an_ordinary_question_publishes(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
    ) -> None:
        """The common case is untouched: no queue, no flag."""
        client.force_login(asker)
        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Could you share the slides?"},
        )

        question = Question.objects.get(talk=talk)
        assert question.status == Question.Status.APPROVED
        assert question.flag_reason == ""

    def test_editing_links_in_sends_the_question_back(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
    ) -> None:
        """
        The obvious bypass, closed.

        Post something innocuous, wait for it to publish, then edit the links in. Re-running
        the heuristics on edit is what stops that.
        """
        question = baker.make(
            Question,
            talk=talk,
            user=asker,
            content="Could you share the slides?",
            status=Question.Status.APPROVED,
        )
        client.force_login(asker)

        client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Deals at buy-now.com and more at deals.xyz"},
        )

        question.refresh_from_db()
        assert question.status == Question.Status.PENDING
        assert question.flag_reason == "many_links"

    def test_an_innocent_edit_leaves_the_question_published(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
    ) -> None:
        """Fixing a typo must not cost the author their place in the thread."""
        question = baker.make(
            Question,
            talk=talk,
            user=asker,
            content="Could you share the slids?",
            status=Question.Status.APPROVED,
        )
        client.force_login(asker)

        response = client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Could you share the slides?"},
        )
        assert response.status_code in (HTTPStatus.OK, HTTPStatus.FOUND)

        question.refresh_from_db()
        assert question.status == Question.Status.APPROVED
