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
from talks.models_qa import Answer, Question, QuestionVote
from users.models import CustomUser


# A deliberate typo, being corrected by an edit.
# cspell:ignore slids


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


@pytest.mark.django_db
class TestPendingIsNotVotable:
    """Voting is limited to questions the voter is actually shown."""

    def test_bystander_cannot_vote_on_a_held_question(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        bystander: CustomUser,
    ) -> None:
        """
        A 200 here would leak that a question exists at an id whose content is withheld.

        The vote would also count: the moderator queue is sorted by votes, so an outsider
        could float a held question to the top of it before anyone had approved it.
        """
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Held for review",
            status=Question.Status.PENDING,
        )
        client.force_login(bystander)

        response = client.post(reverse("question_vote", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert question.votes.count() == 0

    def test_bystander_cannot_vote_on_a_rejected_question(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        bystander: CustomUser,
    ) -> None:
        """A rejected question is author-only too, so it is not votable either."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.REJECTED,
        )
        client.force_login(bystander)

        response = client.post(reverse("question_vote", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert question.votes.count() == 0

    def test_the_author_may_still_vote_on_their_own_held_question(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
    ) -> None:
        """The author sees it, so they may vote on it: the count is right once it is approved."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.PENDING,
        )
        client.force_login(author)

        response = client.post(reverse("question_vote", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.OK
        assert question.votes.count() == 1

    def test_a_moderator_may_vote_on_a_held_question(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        moderator: CustomUser,
    ) -> None:
        """Moderators see the whole queue, so nothing in it is hidden from them."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.PENDING,
        )
        client.force_login(moderator)

        response = client.post(reverse("question_vote", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.OK
        assert question.votes.count() == 1

    def test_public_questions_stay_votable(
        self,
        client: Client,
        talk: Talk,
        author: CustomUser,
        bystander: CustomUser,
    ) -> None:
        """The gate must not break ordinary voting on the published thread."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.APPROVED,
        )
        client.force_login(bystander)

        response = client.post(reverse("question_vote", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.OK
        assert question.votes.count() == 1


@pytest.mark.django_db
class TestAnsweringDoesNotPublishAHeldQuestion:
    """Writing an answer is not a substitute for approving the question."""

    def test_answer_leaves_a_pending_question_pending(
        self,
        talk: Talk,
        author: CustomUser,
        moderator: CustomUser,
    ) -> None:
        """
        ``Answer.save`` used to promote anything not rejected straight to ANSWERED.

        On a held question that published it without the review it was held for, and left the
        ``flag_reason`` in place, so it read as flagged and approved at the same time.
        """
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.PENDING,
            flag_reason="many_links",
        )

        baker.make(Answer, question=question, user=moderator, content="An answer")

        question.refresh_from_db()
        assert question.status == Question.Status.PENDING
        assert question.flag_reason == "many_links"

    def test_answer_still_marks_a_published_question_answered(
        self,
        talk: Talk,
        author: CustomUser,
        moderator: CustomUser,
    ) -> None:
        """The ordinary path is unchanged: answering a published question marks it answered."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.APPROVED,
        )

        baker.make(Answer, question=question, user=moderator, content="An answer")

        question.refresh_from_db()
        assert question.status == Question.Status.ANSWERED

    def test_answer_still_leaves_a_rejected_question_rejected(
        self,
        talk: Talk,
        author: CustomUser,
        moderator: CustomUser,
    ) -> None:
        """Rejected stays rejected, as it always did."""
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            status=Question.Status.REJECTED,
        )

        baker.make(Answer, question=question, user=moderator, content="An answer")

        question.refresh_from_db()
        assert question.status == Question.Status.REJECTED


@pytest.mark.django_db
class TestEditingGoesBackThroughModeration:
    """An edit replaces the body, so the old approval no longer describes what is there."""

    @staticmethod
    def _moderated_talk() -> Talk:
        """Return a talk whose event pre-moderates its Q&A."""
        event = Event.objects.create(
            name="Moderated",
            slug="moderated",
            qa_mode=Event.QAMode.MODERATED,
        )
        return baker.make(Talk, event=event, title="A talk")

    def test_an_edit_on_a_moderated_event_is_held_again(self, client: Client) -> None:
        """
        Even a perfectly innocent rewrite waits for a moderator on a moderated event.

        Otherwise the queue is trivial to bypass: ask something bland, wait to be approved, then
        edit in whatever you actually wanted to say.
        """
        talk = self._moderated_talk()
        author = baker.make(CustomUser, email="author@example.com")
        author.events.add(talk.event)
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="An approved question",
            status=Question.Status.APPROVED,
        )
        client.force_login(author)

        client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Something completely different"},
        )

        question.refresh_from_db()
        assert question.status == Question.Status.PENDING
        assert question.content == "Something completely different"

    def test_an_edit_on_an_open_event_stays_published(self, client: Client) -> None:
        """
        On an open event there is no queue, so a clean edit must not disappear into one.

        Holding it would hide the question until a moderator nobody assigned looked at it.
        """
        event = Event.objects.create(name="Open", slug="open", qa_mode=Event.QAMode.OPEN)
        talk = baker.make(Talk, event=event, title="A talk")
        author = baker.make(CustomUser, email="author@example.com")
        author.events.add(event)
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Could you share the slids?",
            status=Question.Status.APPROVED,
        )
        client.force_login(author)

        client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Could you share the slides?"},
        )

        question.refresh_from_db()
        assert question.status == Question.Status.APPROVED

    def test_the_votes_are_reset_to_the_authors_own(self, client: Client) -> None:
        """
        Votes were cast on the previous wording, so they say nothing about the new one.

        The author's own vote stays, which leaves the question where a newly asked one starts
        rather than below it.
        """
        event = Event.objects.create(name="Open", slug="open", qa_mode=Event.QAMode.OPEN)
        talk = baker.make(Talk, event=event, title="A talk")
        author = baker.make(CustomUser, email="author@example.com")
        author.events.add(event)
        voter = baker.make(CustomUser, email="voter@example.com")
        voter.events.add(event)
        question = baker.make(
            Question,
            talk=talk,
            user=author,
            content="Original wording",
            status=Question.Status.APPROVED,
        )
        QuestionVote.objects.create(question=question, user=author)
        QuestionVote.objects.create(question=question, user=voter)
        client.force_login(author)

        client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Rewritten wording"},
        )

        assert question.votes.count() == 1
        assert question.votes.filter(user=author).exists()

    def test_the_form_warns_before_the_edit(self, client: Client) -> None:
        """The consequences are spelled out on the form, not only in the message afterwards."""
        talk = self._moderated_talk()
        author = baker.make(CustomUser, email="author@example.com")
        author.events.add(talk.event)
        question = baker.make(Question, talk=talk, user=author, content="A question")
        client.force_login(author)

        response = client.get(reverse("question_edit", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.OK
        body = response.content.decode()
        assert "resets the votes" in body
        assert "sends it back to a moderator" in body
