"""Comprehensive tests for talks.views_qa covering uncovered branches."""
# ruff: noqa: F841

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from model_bakery import baker

from talks.models import Talk
from talks.models_qa import Question, QuestionVote
from talks.views_qa import is_moderator
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.fixture()
def user() -> CustomUser:
    """Return a regular test user for Q&A view tests."""
    return baker.make(CustomUser, email="qauser@example.com")


@pytest.fixture()
def staff_user() -> CustomUser:
    """Return a staff user who can moderate questions."""
    return baker.make(CustomUser, email="qastaff@example.com", is_staff=True)


@pytest.fixture()
def talk() -> Talk:
    """Return a talk for attaching questions."""
    return baker.make(Talk, title="QA Talk")


@pytest.fixture()
def question(talk: Talk, user: CustomUser) -> Question:
    """Return a question owned by the test user."""
    return baker.make(Question, talk=talk, user=user, content="Test Q")


@pytest.mark.django_db
class TestVoteNonHtmx:
    """Verify non-HTMX vote requests return a JSON response with vote count."""

    def test_vote_returns_json(self, client: Client, user: CustomUser, question: Question) -> None:
        """Return a JSON payload with vote_count and user_voted for non-HTMX POST."""
        client.force_login(user)
        QuestionVote.objects.filter(question=question, user=user).delete()
        url = reverse("question_vote", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.OK
        data = response.json()
        assert "vote_count" in data
        assert data["user_voted"] is True


@pytest.mark.django_db
class TestQuestionCreateNonHtmx:
    """Verify non-HTMX question creation redirects to the question list."""

    def test_create_redirects(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """Redirect to the question list after successfully creating a question."""
        client.force_login(user)
        url = reverse("question_create", args=[talk.pk])
        response = client.post(url, {"content": "Non-HTMX question"})
        assert response.status_code == HTTPStatus.FOUND


@pytest.mark.django_db
class TestQuestionDeleteNonHtmx:
    """Verify non-HTMX delete redirects and removes the question."""

    def test_delete_redirects(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """Delete the question and redirect to the question list."""
        client.force_login(user)
        url = reverse("question_delete", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        assert not Question.objects.filter(pk=question.pk).exists()


@pytest.mark.django_db
class TestQuestionUpdateView:
    """Tests for QuestionUpdateView."""

    def test_owner_can_edit(self, client: Client, user: CustomUser, question: Question) -> None:
        """Allow the question owner to access the edit form."""
        client.force_login(user)
        url = reverse("question_edit", args=[question.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_non_owner_cannot_edit(self, client: Client, question: Question) -> None:
        """Return 403 when a non-owner tries to edit the question."""
        other = baker.make(CustomUser, email="other@example.com")
        client.force_login(other)
        url = reverse("question_edit", args=[question.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FORBIDDEN

    def test_edit_clears_votes(self, client: Client, user: CustomUser, question: Question) -> None:
        """Remove other users' votes on edit so the updated question starts fresh."""
        other = baker.make(CustomUser, email="voter@example.com")
        QuestionVote.objects.create(question=question, user=user)
        QuestionVote.objects.create(question=question, user=other)
        client.force_login(user)
        url = reverse("question_edit", args=[question.pk])
        response = client.post(url, {"content": "Updated question"}, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        # Other user's vote should be cleared, but user's own vote kept
        assert QuestionVote.objects.filter(question=question, user=user).exists()
        assert not QuestionVote.objects.filter(question=question, user=other).exists()

    def test_edit_non_htmx_redirects(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """Redirect to the question list after editing without HTMX."""
        client.force_login(user)
        url = reverse("question_edit", args=[question.pk])
        response = client.post(url, {"content": "Updated non-htmx"})
        assert response.status_code == HTTPStatus.FOUND

    def test_context_has_status_filter(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """Pass the status_filter query parameter through to the edit form context."""
        client.force_login(user)
        url = reverse("question_edit", args=[question.pk]) + "?status_filter=approved"
        response = client.get(url)
        assert response.context["status_filter"] == "approved"


@pytest.mark.django_db
class TestModerationNonHtmx:
    """Verify moderation views redirect for non-HTMX requests."""

    def test_reject_redirects(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Redirect after rejecting a question via non-HTMX POST."""
        client.force_login(staff_user)
        url = reverse("question_reject", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND

    def test_mark_answered_redirects(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Redirect after marking a question as answered via non-HTMX POST."""
        client.force_login(staff_user)
        url = reverse("question_mark_answered", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND

    def test_approve_redirects(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Redirect after re-approving a rejected question via non-HTMX POST."""
        question.status = Question.Status.REJECTED
        question.save()
        client.force_login(staff_user)
        url = reverse("question_approve", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND


@pytest.mark.django_db
class TestQuestionRedirectView:
    """Tests for question_redirect_view."""

    def test_redirect_by_pretalx_id(self, client: Client, user: CustomUser) -> None:
        """Redirect to the Q&A page when a talk matches the pretalx slug."""
        talk = baker.make(Talk, pretalx_link="https://pretalx.com/event/talk/DEMO2")
        client.force_login(user)
        url = reverse("question_redirect", args=["DEMO2"])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND

    def test_redirect_not_found(self, client: Client, user: CustomUser) -> None:
        """Return 404 when no talk matches the given pretalx slug."""
        client.force_login(user)
        url = reverse("question_redirect", args=["NOPE"])
        response = client.get(url)
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.django_db
class TestQuestionListFilters:
    """Verify question list filters narrow results by status or ownership."""

    def test_filter_mine(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """Show only the current user's questions when status_filter=mine."""
        baker.make(Question, talk=talk, user=user, content="My question")
        other = baker.make(CustomUser, email="x@x.com")
        baker.make(Question, talk=talk, user=other, content="Other question")
        client.force_login(user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=mine"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "My question" in content

    def test_filter_approved(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """Show only approved questions when status_filter=approved."""
        baker.make(Question, talk=talk, status=Question.Status.APPROVED, content="Approved Q")
        baker.make(Question, talk=talk, status=Question.Status.REJECTED, content="Rejected Q")
        client.force_login(user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=approved"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_filter_answered(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """Show only answered questions when status_filter=answered."""
        baker.make(Question, talk=talk, status=Question.Status.ANSWERED, content="Answered Q")
        client.force_login(user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=answered"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_moderator_filter_all(
        self,
        client: Client,
        staff_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Allow moderators to see all questions, including rejected ones."""
        baker.make(Question, talk=talk, status=Question.Status.APPROVED)
        baker.make(Question, talk=talk, status=Question.Status.REJECTED)
        client.force_login(staff_user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=all"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_moderator_filter_rejected(
        self,
        client: Client,
        staff_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Allow moderators to filter for rejected questions only."""
        baker.make(Question, talk=talk, status=Question.Status.REJECTED, content="Rejected!")
        client.force_login(staff_user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=rejected"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_moderator_filter_approved(
        self,
        client: Client,
        staff_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Allow moderators to filter for approved questions only."""
        baker.make(Question, talk=talk, status=Question.Status.APPROVED, content="Approved!")
        client.force_login(staff_user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=approved"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_moderator_filter_answered(
        self,
        client: Client,
        staff_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Allow moderators to filter for answered questions only."""
        baker.make(Question, talk=talk, status=Question.Status.ANSWERED, content="Answered!")
        client.force_login(staff_user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=answered"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_vote_with_status_filter(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """Voting passes through status_filter from POST data."""
        client.force_login(user)
        QuestionVote.objects.filter(question=question, user=user).delete()
        url = reverse("question_vote", args=[question.pk])
        response = client.post(
            url,
            {"status_filter": "approved"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK


class TestIsModeratorFunction:
    """Verify the is_moderator helper function for access control checks."""

    def test_unauthenticated_user(self) -> None:
        """Return False for unauthenticated (anonymous) users."""
        assert is_moderator(AnonymousUser()) is False
