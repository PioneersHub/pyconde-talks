"""
Tests for the Q&A spam heuristics.

Most of the value here is in the negative cases. The rules are cheap to make stricter and expensive
to get wrong: a question wrongly held for review annoys one attendee, but a rule that fires on
ordinary questions fills the queue with noise until moderators stop reading it. The "should not
flag" list is therefore written out as real questions people ask at a Python conference.
"""

import string
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
from talks.spam import count_links, spam_flag_reason
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test import Client
    from pytest_django.fixtures import SettingsWrapper


# Deliberate spam samples and a deliberate typo, not real words.
# cspell:ignore somechannel FREEMONEYNOW spammy slids spammyhandle wechat
# cspell:ignore unterscheidet sich normalisiere korrekt ontact hxxp hxxps codepoint


SHOULD_FLAG = [
    pytest.param("Cheap tickets at buy-now.com and also deals.xyz", "many_links", id="two-hosts"),
    pytest.param(
        "Great offer https://spam.example.com/a http://spam.example.com/b",
        "many_links",
        id="two-urls",
    ),
    pytest.param(
        "Message me on WhatsApp +49 151 2345678 for the recording",
        "contact_handle",
        id="whatsapp-plus-number",
    ),
    pytest.param(
        "Ping me on telegram @spammyhandle for deals",
        "contact_handle",
        id="telegram-plus-handle",
    ),
    pytest.param("Join t.me/somechannel for more", "contact_handle", id="telegram-link"),
    pytest.param("Follow bit.ly/xyz now", "contact_handle", id="shortener"),
    pytest.param(
        "FREEMONEYNOW visit https://scam.example.com",
        "link_and_shouting",
        id="link-plus-shouting",
    ),
    # Shouting spread over short words. The run rule misses it, the ratio rule catches it, and
    # the URL has to be excluded from the ratio or its lowercase characters hide the shouting.
    pytest.param(
        "FREE MONEY CLICK HERE NOW https://scam.example.com",
        "link_and_shouting",
        id="shouting-across-short-words",
    ),
    # Obfuscated hosts, normalized before the links are counted.
    pytest.param(
        "Visit spam dot com and deals dot xyz for free stuff",
        "many_links",
        id="dot-spelled-out",
    ),
    pytest.param("Visit spam[dot]com and deals[dot]xyz", "many_links", id="bracketed-dot"),
    pytest.param(
        "Great deals hxxp://spam.example.com and hxxps://spam2.example.com",
        "many_links",
        id="hxxp-scheme",
    ),
    # An earnings pitch, which needs a second signal (here the link).
    pytest.param(
        "Guaranteed to earn 3000 EUR per month, click https://x.example.com",
        "money_pitch",
        id="earnings-plus-link",
    ),
    # A homoglyph swap: Latin "ontact" behind a Cyrillic capital Es.
    pytest.param("\u0421ontact me for deals", "mixed_script", id="cyrillic-homoglyph"),
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
    # A messaging platform is an ordinary topic at a Python conference: there are well-known
    # libraries for all of them. Only the platform named next to an actual handle or phone
    # number is an advert, so the bare product name deliberately does not flag.
    pytest.param("Does this work with the Telegram Bot API?", id="telegram-as-a-topic"),
    pytest.param("We ship a WhatsApp integration - how would you test it?", id="whatsapp-topic"),
    pytest.param("Is there a WeChat SDK for Python you would recommend?", id="wechat-topic"),
    # Module paths that end in a listed TLD. Counting these as links meant two of them in one
    # question was enough to hold it, which at a PyData conference is a common sentence.
    pytest.param("How do scipy.io and pandas.io differ for mat files?", id="dotted-module-paths"),
    pytest.param("Is socket.io supported, or only tensorflow.io?", id="more-module-paths"),
    pytest.param(
        "Wie unterscheidet sich das von https://scikit-learn.org?",
        id="german-with-one-link",
    ),
    # The de-obfuscation must not read ordinary uses of the word "dot" as a hidden host, and must
    # not mangle "dotted" into ".ted" while doing it.
    pytest.param("Is the dot product computed on the GPU here?", id="dot-product"),
    pytest.param("We have dotted module paths everywhere - a problem?", id="dotted-word"),
    # Another script quoted in its own words is not a homoglyph swap. Only mixing inside one
    # word is, so a German question about Cyrillic normalization has to pass.
    pytest.param(
        "Wie normalisiere ich \u041a\u0438\u0440\u0438\u043b\u043b\u0438\u0446\u0430 korrekt?",
        id="quoted-other-script",
    ),
    # Numbers and "per week" appear in perfectly ordinary throughput questions.
    pytest.param("Does it scale to 5000 requests per second?", id="throughput-number"),
    pytest.param("Can I make 100 requests per week on the free tier?", id="rate-limit-question"),
    pytest.param("Great talk! Any thoughts on POSTGRESQL vs SQLite?", id="shouted-product-name"),
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


# ASCII letters, digits and spaces, spelled out. Narrow on purpose: no dot, colon, at-sign or
# slash means no link, host, handle or shortener; no brackets means the de-obfuscation cannot build
# one either; and staying inside ASCII rules out the homoglyph rule, which needs no punctuation at
# all and would otherwise make the property below false for random Unicode.
PLAIN_ALPHABET = string.ascii_letters + string.digits + " "


@given(st.text(alphabet=st.sampled_from(PLAIN_ALPHABET), max_size=200))
def test_plain_ascii_prose_is_never_flagged(content: str) -> None:
    """
    A property check on regex over-reach.

    Plain words and numbers contain no host, URL, handle or shortener, and shouting alone is never
    enough on its own, so nothing here should ever trip a rule. Guards against a future pattern that
    is too eager.
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

        Post something innocuous, wait for it to publish, then edit the links in. Re-running the
        heuristics on edit is what stops that.
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

    def test_editing_links_into_an_answered_question_sends_it_back(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
    ) -> None:
        """
        The same bypass, one status along.

        A question a moderator has marked answered is still shown to everyone, so gating the re-
        check on APPROVED alone left this route open: get answered, then edit the links in.
        """
        question = baker.make(
            Question,
            talk=talk,
            user=asker,
            content="Could you share the slides?",
            status=Question.Status.ANSWERED,
        )
        client.force_login(asker)

        client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Deals at buy-now.com and more at deals.xyz"},
        )

        question.refresh_from_db()
        assert question.status == Question.Status.PENDING
        assert question.flag_reason == "many_links"


def test_a_written_out_link_counts_once() -> None:
    """
    A scheme URL must not also be counted as a bare host.

    "https://example.com" contains "example.com", so counting both patterns over the raw text made a
    single citation look like two links and held an ordinary question for review.
    """
    assert count_links("See https://example.com for the docs") == 1
    assert spam_flag_reason("See https://example.com for the docs") == ""


def test_two_written_out_links_still_count_twice() -> None:
    """The de-duplication must not swallow a genuine second link."""
    expected = 2
    assert count_links("https://a.example.com and https://b.example.com") == expected
