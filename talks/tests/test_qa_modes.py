"""
Tests for the per-event Q&A modes.

The four modes are a spectrum of how much attention the organizers can give the queue: open
while the event runs and volunteers are watching, moderated when spam appears, frozen once
nobody is watching, disabled when the Q&A was never wanted.
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
from utils.test_perf import assert_no_n_plus_one


if TYPE_CHECKING:
    from django.test import Client


def _talk_with_qa(mode: str) -> Talk:
    """Return a talk on an event running its Q&A in *mode*."""
    event = Event.objects.create(name=f"Event {mode}", slug=f"event-{mode}", qa_mode=mode)
    return baker.make(Talk, event=event, title="A talk")


def _member_of(talk: Talk, email: str, *, staff: bool = False) -> CustomUser:
    """Return a user with access to the talk's event."""
    user = baker.make(CustomUser, email=email, is_staff=staff)
    user.events.add(talk.event)
    return user


@pytest.mark.django_db
class TestOpenMode:
    """The default: questions appear as soon as they are asked."""

    def test_is_the_default(self) -> None:
        """An event that says nothing about Q&A behaves as it always has."""
        event = Event.objects.create(name="Default", slug="default")
        assert event.qa_mode == Event.QAMode.OPEN

    def test_question_is_published_immediately(self, client: Client) -> None:
        """No queue, no waiting."""
        talk = _talk_with_qa(Event.QAMode.OPEN)
        client.force_login(_member_of(talk, "asker@example.com"))

        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "A straightforward question"},
        )

        question = Question.objects.get(talk=talk)
        assert question.status == Question.Status.APPROVED


@pytest.mark.django_db
class TestModeratedMode:
    """Questions wait for a moderator before anyone else sees them."""

    def test_new_question_is_held(self, client: Client) -> None:
        """The submission succeeds, but the question is not published yet."""
        talk = _talk_with_qa(Event.QAMode.MODERATED)
        client.force_login(_member_of(talk, "asker@example.com"))

        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "A question awaiting review"},
        )

        question = Question.objects.get(talk=talk)
        assert question.status == Question.Status.PENDING

    def test_other_attendees_do_not_see_it_until_approved(self, client: Client) -> None:
        """The whole point: the queue is private until a moderator works through it."""
        talk = _talk_with_qa(Event.QAMode.MODERATED)
        asker = _member_of(talk, "asker@example.com")
        bystander = _member_of(talk, "bystander@example.com")

        client.force_login(asker)
        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Held back for now"},
        )

        client.force_login(bystander)
        listing = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert b"Held back for now" not in listing.content

        question = Question.objects.get(talk=talk)
        question.approve()

        listing = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert b"Held back for now" in listing.content

    def test_the_form_says_so(self, client: Client) -> None:
        """Tell people their question will be reviewed, rather than leaving them guessing."""
        talk = _talk_with_qa(Event.QAMode.MODERATED)
        client.force_login(_member_of(talk, "asker@example.com"))

        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert b"reviews questions before they appear" in response.content


@pytest.mark.django_db
class TestFrozenMode:
    """The archive stays readable; nothing new can be added."""

    def test_new_questions_are_refused(self, client: Client) -> None:
        """A frozen Q&A stores nothing, however well-formed the POST."""
        talk = _talk_with_qa(Event.QAMode.FROZEN)
        client.force_login(_member_of(talk, "asker@example.com"))

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Too late"},
        )

        assert response.status_code in (HTTPStatus.CONFLICT, HTTPStatus.FOUND)
        assert Question.objects.filter(talk=talk).exists() is False

    def test_existing_questions_are_still_readable(self, client: Client) -> None:
        """Freezing preserves the thread rather than hiding it."""
        talk = _talk_with_qa(Event.QAMode.FROZEN)
        baker.make(Question, talk=talk, content="Asked while it was open")
        client.force_login(_member_of(talk, "reader@example.com"))

        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert response.status_code == HTTPStatus.OK
        assert b"Asked while it was open" in response.content

    def test_the_form_is_replaced_by_an_explanation(self, client: Client) -> None:
        """Say why there is no form, rather than silently removing it."""
        talk = _talk_with_qa(Event.QAMode.FROZEN)
        client.force_login(_member_of(talk, "reader@example.com"))

        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert b"Questions are closed for this talk" in response.content
        assert b'id="question-form"' not in response.content


@pytest.mark.django_db
class TestDisabledMode:
    """The Q&A looks absent, not merely closed."""

    def test_the_page_is_gone(self, client: Client) -> None:
        """A disabled Q&A 404s rather than rendering an empty shell."""
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        client.force_login(_member_of(talk, "reader@example.com"))

        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_posting_is_refused(self, client: Client) -> None:
        """Nor can a question be posted to the endpoint directly."""
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        client.force_login(_member_of(talk, "asker@example.com"))

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Anyone there?"},
        )
        assert response.status_code == HTTPStatus.NOT_FOUND
        assert Question.objects.filter(talk=talk).exists() is False

    def test_even_moderators_get_nothing(self, client: Client) -> None:
        """
        Disabling hides the content from everyone.

        A moderator with a stale tab polls every ten seconds; the switch has to take effect
        there too, not just for ordinary attendees.
        """
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        baker.make(Question, talk=talk, content="Previously asked")
        client.force_login(_member_of(talk, "mod@example.com", staff=True))

        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        assert response.status_code == HTTPStatus.NOT_FOUND

    def test_the_talk_page_does_not_link_to_it(self, client: Client) -> None:
        """No point offering a link to a 404."""
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        client.force_login(_member_of(talk, "reader@example.com"))

        response = client.get(reverse("talk_detail", kwargs={"pk": talk.pk}))
        assert reverse("talk_questions", kwargs={"talk_id": talk.pk}).encode() not in (
            response.content
        )


@pytest.mark.django_db
class TestModeHelpers:
    """The derived properties keep the rules in one place."""

    @pytest.mark.parametrize(
        ("mode", "visible", "accepts", "holds"),
        [
            (Event.QAMode.OPEN, True, True, False),
            (Event.QAMode.MODERATED, True, True, True),
            (Event.QAMode.FROZEN, True, False, False),
            (Event.QAMode.DISABLED, False, False, False),
        ],
    )
    def test_truth_table(
        self,
        mode: str,
        visible: bool,  # noqa: FBT001
        accepts: bool,  # noqa: FBT001
        holds: bool,  # noqa: FBT001
    ) -> None:
        """Each mode answers the three questions the views actually ask."""
        event = baker.make(Event, qa_mode=mode)
        assert event.qa_visible is visible
        assert event.qa_accepts_questions is accepts
        assert event.qa_holds_for_review is holds

    def test_mode_checks_do_not_cause_a_query_per_question(self, client: Client) -> None:
        """
        The event is fetched once, not once per mode check.

        Every check reads ``talk.event``, so without select_related the list view would issue
        an extra query for each one.
        """
        talk = _talk_with_qa(Event.QAMode.OPEN)
        for i in range(10):
            baker.make(Question, talk=talk, content=f"Question {i}")
        client.force_login(_member_of(talk, "reader@example.com"))

        url = reverse("talk_questions", kwargs={"talk_id": talk.pk})
        with assert_no_n_plus_one():
            client.get(url)
