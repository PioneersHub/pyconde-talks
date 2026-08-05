"""
Tests for the Q&A rate limiter.

The cache is process-global and shared across the whole test run, so these depend on the autouse
``_clear_cache`` fixture in the root conftest. Without it a counter would leak between tests and,
under ``--random-order``, fail only on some seeds.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from talks.models_qa import Question
from talks.ratelimit import (
    RateLimit,
    claim,
    consume,
    is_rate_limited,
    refund,
    seconds_until_reset,
)
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test import Client
    from django.test.client import _MonkeyPatchedWSGIResponse
    from pytest_django.fixtures import SettingsWrapper


# A small allowance so the tests can reach it in a couple of requests.
ALLOWANCE = 2


@pytest.fixture
def talk() -> Talk:
    """Return a talk on an event with an open Q&A."""
    event = Event.objects.create(name="Event", slug="event")
    return baker.make(Talk, event=event, title="A talk")


@pytest.fixture
def second_talk(talk: Talk) -> Talk:
    """Return another talk on the same event."""
    return baker.make(Talk, event=talk.event, title="Another talk")


@pytest.fixture
def asker(talk: Talk) -> CustomUser:
    """Return a user with access to the talk's event."""
    user = baker.make(CustomUser, email="asker@example.com")
    user.events.add(talk.event)
    return user


def _ask(client: Client, talk: Talk, content: str) -> _MonkeyPatchedWSGIResponse:
    """
    Post a question as the real form does, over HTMX, and return the response.

    The header matters: a plain POST gets the flash-and-redirect branch, so an error arrives as a
    302 and the status code under test would be lost.
    """
    return client.post(
        reverse("question_create", kwargs={"talk_id": talk.pk}),
        {"content": content},
        HTTP_HX_REQUEST="true",
    )


class TestRateLimitPrimitives:
    """The small cache-backed helpers the view builds on."""

    def test_allowance_is_consumed_then_exhausted(self) -> None:
        """Counting up to the limit is fine; the next check reports exhausted."""
        rule = RateLimit(limit=2, window_seconds=60)
        assert is_rate_limited("scope", "someone", rule) is False

        consume("scope", "someone", rule)
        assert is_rate_limited("scope", "someone", rule) is False

        consume("scope", "someone", rule)
        assert is_rate_limited("scope", "someone", rule) is True

    def test_identities_do_not_share_an_allowance(self) -> None:
        """One noisy account must not spend anyone else's quota."""
        rule = RateLimit(limit=1, window_seconds=60)
        consume("scope", "noisy", rule)

        assert is_rate_limited("scope", "noisy", rule) is True
        assert is_rate_limited("scope", "quiet", rule) is False

    def test_scopes_are_independent(self) -> None:
        """The per-talk and overall allowances are counted separately."""
        rule = RateLimit(limit=1, window_seconds=60)
        consume("scope-a", "someone", rule)

        assert is_rate_limited("scope-a", "someone", rule) is True
        assert is_rate_limited("scope-b", "someone", rule) is False

    def test_seconds_until_reset_is_within_the_window(self) -> None:
        """The wait told to the user is bounded by the window it refers to."""
        rule = RateLimit(limit=1, window_seconds=600)
        remaining = seconds_until_reset(rule)
        assert 1 <= remaining <= rule.window_seconds


@pytest.mark.django_db
class TestQuestionRateLimit:
    """The limiter as it applies to asking questions."""

    def test_questions_under_the_limit_all_succeed(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """The limit is generous enough that ordinary use never meets it."""
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 3
        client.force_login(asker)

        for i in range(3):
            _ask(client, talk, f"Question number {i}")

        assert Question.objects.filter(talk=talk).count() == 3  # noqa: PLR2004

    def test_the_next_question_is_refused(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """Once the allowance is spent the question is not stored."""
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 2
        client.force_login(asker)

        _ask(client, talk, "First")
        _ask(client, talk, "Second")
        response = _ask(client, talk, "Third")

        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert Question.objects.filter(talk=talk).count() == 2  # noqa: PLR2004

    def test_the_refusal_explains_the_wait(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """A bare 429 is unhelpful; say roughly how long to wait."""
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 1
        client.force_login(asker)

        _ask(client, talk, "First")
        response = _ask(client, talk, "Second")

        assert b"Please wait about" in response.content

    def test_the_per_talk_limit_does_not_spill_to_another_talk(
        self,
        client: Client,
        talk: Talk,
        second_talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """Being chatty about one talk must not silence someone on the next."""
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 1
        settings.QA_QUESTION_RATE_LIMIT_OVERALL = 100
        client.force_login(asker)

        _ask(client, talk, "First here")
        _ask(client, talk, "Second here")
        _ask(client, second_talk, "First there")

        assert Question.objects.filter(talk=talk).count() == 1
        assert Question.objects.filter(talk=second_talk).count() == 1

    def test_the_overall_limit_catches_spreading_across_talks(
        self,
        client: Client,
        talk: Talk,
        second_talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """The per-talk limit alone would let someone spray the whole schedule."""
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 100
        settings.QA_QUESTION_RATE_LIMIT_OVERALL = 2
        client.force_login(asker)

        _ask(client, talk, "One")
        _ask(client, talk, "Two")
        response = _ask(client, second_talk, "Three, on another talk")

        assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert Question.objects.count() == 2  # noqa: PLR2004

    def test_moderators_are_exempt(
        self,
        client: Client,
        talk: Talk,
        settings: SettingsWrapper,
    ) -> None:
        """Moderators post repeatedly by nature, and would have to unpick their own limit."""
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 1
        moderator = baker.make(CustomUser, email="mod@example.com", is_staff=True)
        moderator.events.add(talk.event)
        client.force_login(moderator)

        _ask(client, talk, "First")
        _ask(client, talk, "Second")

        assert Question.objects.filter(talk=talk).count() == 2  # noqa: PLR2004

    def test_a_rejected_draft_does_not_spend_the_allowance(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """
        The allowance is consumed on success, not on attempt.

        Otherwise an over-long question, or one that trips the captcha, would quietly cost the
        author part of their quota for a question that was never stored.
        """
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 1
        client.force_login(asker)

        # Too long for the field, so the form rejects it.
        _ask(client, talk, "x" * 5000)
        assert Question.objects.filter(talk=talk).count() == 0

        response = _ask(client, talk, "A valid question")
        assert response.status_code != HTTPStatus.TOO_MANY_REQUESTS
        assert Question.objects.filter(talk=talk).count() == 1

    def test_a_dummy_cache_disables_the_limiter(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """
        Documents the degraded mode rather than pretending it cannot happen.

        With a cache that stores nothing the limiter never trips. Worth knowing: it is what a
        misconfigured CACHES setting looks like in production.
        """
        settings.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"},
        }
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 1
        client.force_login(asker)

        _ask(client, talk, "First")
        _ask(client, talk, "Second")

        assert Question.objects.filter(talk=talk).count() == 2  # noqa: PLR2004


@pytest.mark.django_db
class TestClaimIsAtomic:
    """The allowance is counted before the content is checked, then given back if unused."""

    def test_a_rejected_draft_is_refunded(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """
        A question that never got stored must not cost part of the allowance.

        The claim has to happen before validation for the limit to be atomic, so the refund is what
        keeps an over-long or captcha-failing draft from quietly eating a slot.
        """
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = ALLOWANCE
        client.force_login(asker)
        url = reverse("question_create", kwargs={"talk_id": talk.pk})

        # Two rejected drafts: over the content limit, so they are refused and refunded.
        for _ in range(2):
            rejected = client.post(url, {"content": "x" * 5000}, HTTP_HX_REQUEST="true")
            assert rejected.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

        # The full allowance is still there.
        for index in range(ALLOWANCE):
            accepted = client.post(url, {"content": f"A real question {index}"})
            assert accepted.status_code == HTTPStatus.FOUND

        assert Question.objects.filter(talk=talk).count() == ALLOWANCE

    def test_the_limit_still_bites_after_refunds(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        settings: SettingsWrapper,
    ) -> None:
        """Refunding must not turn the limit off: the third real question is still refused."""
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = ALLOWANCE
        client.force_login(asker)
        url = reverse("question_create", kwargs={"talk_id": talk.pk})

        client.post(url, {"content": "x" * 5000}, HTTP_HX_REQUEST="true")
        client.post(url, {"content": "First real question"})
        client.post(url, {"content": "Second real question"})
        third = client.post(url, {"content": "Third real question"}, HTTP_HX_REQUEST="true")

        assert third.status_code == HTTPStatus.TOO_MANY_REQUESTS
        assert Question.objects.filter(talk=talk).count() == ALLOWANCE


class TestClaimSemantics:
    """``claim`` counts first and judges the result, so a burst cannot slip past a peek."""

    def test_claim_allows_exactly_the_allowance(self) -> None:
        """Three claims against a limit of three all pass; the fourth does not."""
        rule = RateLimit(limit=3, window_seconds=600)
        results = [claim("test_scope", "someone", rule) for _ in range(4)]
        assert results == [True, True, True, False]

    def test_an_over_limit_claim_keeps_the_window_shut(self) -> None:
        """
        A refused attempt still counts.

        Otherwise hammering the endpoint would reset the allowance on every refusal, which is the
        opposite of what a rate limit is for.
        """
        rule = RateLimit(limit=1, window_seconds=600)
        assert claim("test_scope", "hammerer", rule) is True
        for _ in range(5):
            assert claim("test_scope", "hammerer", rule) is False
        assert claim("test_scope", "hammerer", rule) is False

    def test_refund_gives_back_exactly_one(self) -> None:
        """A refund restores one slot, not the whole window."""
        rule = RateLimit(limit=2, window_seconds=600)
        assert claim("test_scope", "refunded", rule) is True
        assert claim("test_scope", "refunded", rule) is True
        assert claim("test_scope", "refunded", rule) is False

        refund("test_scope", "refunded", rule)
        assert claim("test_scope", "refunded", rule) is False

    def test_refund_on_an_expired_window_is_a_no_op(self) -> None:
        """
        Refunding into a window that has rolled over must not seed it below zero.

        A negative count would hand the next caller a free extra question.
        """
        rule = RateLimit(limit=2, window_seconds=600)
        refund("test_scope", "never-claimed", rule)
        assert claim("test_scope", "never-claimed", rule) is True
        assert claim("test_scope", "never-claimed", rule) is True
        assert claim("test_scope", "never-claimed", rule) is False
