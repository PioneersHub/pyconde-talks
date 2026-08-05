"""
Tests for the pending (pre-moderation) question state.

Nothing produces a pending question yet - the per-event Q&A modes and the spam heuristics do
that - so these cover the state itself: who can see one, and how it moves out again.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from talks.models_qa import Question
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test import Client


@pytest.fixture
def talk() -> Talk:
    """Return a talk on an event its members can reach."""
    event = Event.objects.create(name="Event", slug="event")
    return baker.make(Talk, event=event, title="A talk")


@pytest.fixture
def author(talk: Talk) -> CustomUser:
    """Return a user with access to the talk's event."""
    user = baker.make(CustomUser, email="author@example.com")
    user.events.add(talk.event)
    return user


@pytest.fixture
def bystander(talk: Talk) -> CustomUser:
    """Return a second user with access, who did not ask the question."""
    user = baker.make(CustomUser, email="bystander@example.com")
    user.events.add(talk.event)
    return user


@pytest.fixture
def moderator(talk: Talk) -> CustomUser:
    """Return a staff user, who moderates."""
    user = baker.make(CustomUser, email="mod@example.com", is_staff=True)
    user.events.add(talk.event)
    return user


@pytest.mark.django_db
class TestPendingVisibility:
    """A held question is visible to its author and to moderators, and to nobody else."""

    def test_author_sees_their_own_pending_question(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
    ) -> None:
        """
        The author must see that the question was received.

        Otherwise submitting into a moderated Q&A looks exactly like the post being dropped.
        """
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Held for review",
            status=Question.Status.PENDING,
        )
        client.force_login(author)
        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert question.content.encode() in response.content

    def test_other_users_do_not_see_a_pending_question(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        bystander: CustomUser,
    ) -> None:
        """Holding a question back is the entire point of pre-moderation."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Held for review",
            status=Question.Status.PENDING,
        )
        client.force_login(bystander)
        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert question.content.encode() not in response.content

    def test_moderators_see_pending_questions(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        moderator: CustomUser,
    ) -> None:
        """Someone has to be able to work the queue."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Held for review",
            status=Question.Status.PENDING,
        )
        client.force_login(moderator)
        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert question.content.encode() in response.content

    def test_pending_filter_shows_a_regular_user_only_their_own(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        bystander: CustomUser,
    ) -> None:
        """
        The filter is offered to moderators, but the value is not privileged.

        A regular user passing ``status_filter=pending`` by hand gets their own held
        questions, not everyone's.
        """
        mine = baker.make(
            Question,
            talk=talk,
            user=bystander,
            content="Mine held",
            status=Question.Status.PENDING,
        )
        theirs = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Theirs held",
            status=Question.Status.PENDING,
        )
        client.force_login(bystander)
        response = client.get(
            reverse("talk_questions", kwargs={"talk_id": talk.pk}),
            {"status_filter": "pending"},
        )
        assert mine.content.encode() in response.content
        assert theirs.content.encode() not in response.content


@pytest.mark.django_db
class TestPendingTransitions:
    """Moving a question into and out of the queue."""

    def test_mark_as_pending_records_the_reason(self, talk: Talk, author: CustomUser) -> None:
        """The reason is kept so a misfiring heuristic can be identified from the data."""
        question = baker.make(Question, talk=talk, user=author)
        question.mark_as_pending("many_links")

        question.refresh_from_db()
        assert question.status == Question.Status.PENDING
        assert question.flag_reason == "many_links"

    def test_approving_clears_the_flag(self, talk: Talk, author: CustomUser) -> None:
        """A moderator saying yes settles it; the flag should not linger in the admin."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.PENDING,
            flag_reason="many_links",
        )
        question.approve()

        question.refresh_from_db()
        assert question.status == Question.Status.APPROVED
        assert question.flag_reason == ""

    def test_moderator_can_approve_a_pending_question(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        moderator: CustomUser,
        bystander: CustomUser,
    ) -> None:
        """Approving publishes the question to everyone, reusing the existing endpoint."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Held then published",
            status=Question.Status.PENDING,
        )
        client.force_login(moderator)
        response = client.post(reverse("question_approve", kwargs={"question_id": question.pk}))
        assert response.status_code in (HTTPStatus.OK, HTTPStatus.FOUND)

        question.refresh_from_db()
        assert question.status == Question.Status.APPROVED

        client.force_login(bystander)
        listing = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert question.content.encode() in listing.content

    def test_pending_queryset_helper(self, talk: Talk, author: CustomUser) -> None:
        """``pending()`` matches the other status helpers on the queryset."""
        held = baker.make(Question, talk=talk, user=author, status=Question.Status.PENDING)
        baker.make(Question, talk=talk, user=author, status=Question.Status.APPROVED)

        assert list(Question.objects.pending()) == [held]

    @pytest.mark.parametrize("status", [choice for choice, _ in Question.Status.choices])
    def test_every_status_survives_validation(
        self,
        talk: Talk,
        author: CustomUser,
        status: str,
    ) -> None:
        """
        Every declared status fits the field, checked through ``full_clean``.

        The field was widened to 20 to take "pending" with room to spare. SQLite does not
        enforce max_length, so a future choice that overflowed it would otherwise pass the
        suite here and only fail on Postgres in production.
        """
        question = baker.make(Question, talk=talk, user=author, status=status)
        question.full_clean()

        question.refresh_from_db()
        assert question.status == status
