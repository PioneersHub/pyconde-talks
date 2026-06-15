"""Tests for Q&A views (QuestionListView, voting, moderation)."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.models import Talk
from talks.models_qa import Question, QuestionVote
from users.models import CustomUser
from utils.test_perf import assert_no_n_plus_one


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.fixture()
def event() -> Event:
    """Return the event the test talk and users share (talks are event-scoped)."""
    return Event.objects.create(slug="qa", name="QA", year=2099)


@pytest.fixture()
def user(event: Event) -> CustomUser:
    """Create a regular user with access to the test event."""
    user = baker.make(CustomUser, email="user@example.com")
    user.events.add(event)
    return user


@pytest.fixture()
def staff_user(event: Event) -> CustomUser:
    """Create a staff user with access to the test event."""
    user = baker.make(CustomUser, email="staff@example.com", is_staff=True)
    user.events.add(event)
    return user


@pytest.fixture()
def talk(event: Event) -> Talk:
    """Create a talk in the test event."""
    return baker.make(Talk, title="Test Talk", event=event)


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

    def test_question_list_has_auto_refresh_polling(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """The question list includes HTMX polling attributes for auto-refresh."""
        client.force_login(user)
        url = reverse("talk_questions", args=[talk.pk])
        response = client.get(url)
        content = response.content.decode()
        assert 'hx-trigger="every 10s"' in content
        assert 'hx-swap="morph:outerHTML"' in content
        assert 'hx-ext="morph"' in content

    def test_question_list_polling_preserves_status_filter(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """The polling URL includes the active status filter."""
        client.force_login(user)
        url = reverse("talk_questions", args=[talk.pk]) + "?status_filter=approved"
        response = client.get(url)
        content = response.content.decode()
        assert "status_filter=approved" in content

    def test_question_list_no_n_plus_one(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """Many questions must not trigger a per-row query for user or vote count."""
        for i in range(8):
            asker = baker.make(CustomUser, email=f"asker{i}@example.com")
            baker.make(Question, talk=talk, user=asker, content=f"Q{i}")

        client.force_login(user)
        with assert_no_n_plus_one():
            response = client.get(reverse("talk_questions", args=[talk.pk]))
        assert response.status_code == HTTPStatus.OK


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
        assert response.status_code == HTTPStatus.FORBIDDEN

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

    def test_mark_answered_preserves_status_filter(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Marking a question as answered keeps the active status filter."""
        client.force_login(staff_user)
        url = reverse("question_mark_answered", args=[question.pk])
        response = client.post(
            url,
            {"status_filter": "approved"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        # The returned fragment should have "approved" selected in the filter dropdown
        assert 'value="approved"' in content
        assert "selected" in content.split('value="approved"')[1].split(">")[0]

    def test_reject_preserves_status_filter(
        self,
        client: Client,
        staff_user: CustomUser,
        question: Question,
    ) -> None:
        """Rejecting a question keeps the active status filter."""
        client.force_login(staff_user)
        url = reverse("question_reject", args=[question.pk])
        response = client.post(
            url,
            {"status_filter": "approved"},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert 'value="approved"' in content
        assert "selected" in content.split('value="approved"')[1].split(">")[0]


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
        event: Event,
    ) -> None:
        """A non-owner who can see the talk still can't delete someone else's question."""
        other_user: CustomUser = baker.make(CustomUser, email="other@example.com")
        other_user.events.add(event)
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


@pytest.mark.django_db
class TestQuestionEditAccess:
    """The edit endpoint must respect event access, not just ownership."""

    def test_owner_without_event_access_gets_404(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """A user who owns a question but lost access to its talk's event cannot edit it."""
        # ``user`` belongs to the shared ``event`` fixture, not to this one.
        other_event = Event.objects.create(slug="other", name="Other", year=2099)
        other_talk = baker.make(Talk, title="Inaccessible", event=other_event)
        question = baker.make(Question, talk=other_talk, user=user, content="mine")

        client.force_login(user)
        url = reverse("question_edit", args=[question.pk])

        assert client.get(url).status_code == HTTPStatus.NOT_FOUND
        assert client.post(url, {"content": "edited"}).status_code == HTTPStatus.NOT_FOUND
        question.refresh_from_db()
        assert question.content == "mine"  # unchanged

    def test_owner_with_event_access_can_edit(
        self,
        client: Client,
        user: CustomUser,
        question: Question,
    ) -> None:
        """The normal path still works: an owner with access reaches the edit form."""
        client.force_login(user)
        url = reverse("question_edit", args=[question.pk])
        assert client.get(url).status_code == HTTPStatus.OK
