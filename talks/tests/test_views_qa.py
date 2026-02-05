"""Tests for Q&A views (QuestionListView, voting, moderation)."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from model_bakery import baker

from talks.models import Talk
from talks.models_qa import Question, QuestionVote
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.fixture()
def user() -> CustomUser:
    """Create a regular user for testing."""
    return baker.make(CustomUser, email="user@example.com")


@pytest.fixture()
def staff_user() -> CustomUser:
    """Create a staff user for testing."""
    return baker.make(CustomUser, email="staff@example.com", is_staff=True)


@pytest.fixture()
def talk() -> Talk:
    """Create a talk for testing."""
    return baker.make(Talk, title="Test Talk")


@pytest.fixture()
def question(talk: Talk, user: CustomUser) -> Question:
    """Create a question for testing."""
    return baker.make(Question, talk=talk, user=user, content="What is Python?")


@pytest.mark.django_db
class TestQuestionListView:
    """Tests for QuestionListView."""

    def test_authenticated_user_can_view_questions(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Authenticated users can view the question list."""
        client.force_login(user)
        url = reverse("talk_questions", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Questions for" in response.content.decode()

    def test_unauthenticated_user_redirected(
        self,
        client: Client,
        talk: Talk,
    ) -> None:
        """Unauthenticated users are redirected to login."""
        url = reverse("talk_questions", args=[talk.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
        assert "login" in response.headers.get("Location", "")

    def test_htmx_request_returns_fragment(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """HTMX requests return only the question list fragment."""
        client.force_login(user)
        url = reverse("talk_questions", args=[talk.pk])
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        # Fragment should not contain full HTML structure
        content = response.content.decode()
        assert "<html>" not in content.lower()


@pytest.mark.django_db
class TestQuestionVoting:
    """Tests for question voting functionality."""

    def test_vote_creates_vote(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """Voting on a question creates a QuestionVote."""
        client.force_login(user)
        # First, clear any auto-created vote from question fixture
        QuestionVote.objects.filter(question=question, user=user).delete()

        url = reverse("question_vote", args=[question.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        assert QuestionVote.objects.filter(question=question, user=user).exists()

    def test_vote_toggle_removes_vote(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """Voting again on an already-voted question removes the vote."""
        client.force_login(user)
        # Ensure vote exists
        QuestionVote.objects.get_or_create(question=question, user=user)

        url = reverse("question_vote", args=[question.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        assert not QuestionVote.objects.filter(question=question, user=user).exists()

    def test_unauthenticated_user_cannot_vote(
        self,
        client: Client,
        question: Question,
    ) -> None:
        """Unauthenticated users cannot vote."""
        url = reverse("question_vote", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND


@pytest.mark.django_db
class TestQuestionModeration:
    """Tests for question moderation actions."""

    def test_moderator_can_reject_question(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Staff users can reject questions."""
        client.force_login(staff_user)
        url = reverse("question_reject", args=[question.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        question.refresh_from_db()
        assert question.status == Question.Status.REJECTED

    def test_regular_user_cannot_reject_question(
        self,
        client: Client,
        question: Question,
    ) -> None:
        """Regular users cannot reject questions."""
        # Create another user who doesn't own the question
        other_user: CustomUser = baker.make(CustomUser, email="other@example.com")
        client.force_login(other_user)
        url = reverse("question_reject", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND  # Redirected (not authorized)

    def test_moderator_can_mark_answered(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Staff users can mark questions as answered."""
        client.force_login(staff_user)
        url = reverse("question_mark_answered", args=[question.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        question.refresh_from_db()
        assert question.status == Question.Status.ANSWERED

    def test_moderator_can_approve_question(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Staff users can approve rejected questions."""
        question.status = Question.Status.REJECTED
        question.save()
        client.force_login(staff_user)
        url = reverse("question_approve", args=[question.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        question.refresh_from_db()
        assert question.status == Question.Status.APPROVED


@pytest.mark.django_db
class TestQuestionCreate:
    """Tests for creating questions."""

    def test_authenticated_user_can_create_question(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Authenticated users can create questions."""
        client.force_login(user)
        url = reverse("question_create", args=[talk.pk])
        response = client.post(
            url,
            {"content": "How do I use HTMX?"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK
        assert Question.objects.filter(talk=talk, content="How do I use HTMX?").exists()

    def test_question_auto_votes_for_author(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Creating a question automatically adds the author's vote."""
        client.force_login(user)
        url = reverse("question_create", args=[talk.pk])
        client.post(
            url,
            {"content": "What is Django?"},
            HTTP_HX_REQUEST="true",
        )
        question = Question.objects.get(talk=talk, content="What is Django?")
        assert QuestionVote.objects.filter(question=question, user=user).exists()


@pytest.mark.django_db
class TestQuestionDelete:
    """Tests for deleting questions."""

    def test_owner_can_delete_question(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """Question owners can delete their questions."""
        client.force_login(user)
        url = reverse("question_delete", args=[question.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        assert not Question.objects.filter(pk=question.pk).exists()

    def test_non_owner_cannot_delete_question(
        self,
        client: Client,
        question: Question,
    ) -> None:
        """Non-owners cannot delete questions."""
        other_user: CustomUser = baker.make(CustomUser, email="other@example.com")
        client.force_login(other_user)
        url = reverse("question_delete", args=[question.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FORBIDDEN
        assert Question.objects.filter(pk=question.pk).exists()

    def test_moderator_can_delete_any_question(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Staff users can delete any question."""
        client.force_login(staff_user)
        url = reverse("question_delete", args=[question.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        assert not Question.objects.filter(pk=question.pk).exists()
