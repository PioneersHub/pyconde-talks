"""
Tests for the per-event Q&A modes.

The four modes are a spectrum of how much attention the organizers can give the queue: open while
the event runs and volunteers are watching, moderated when spam appears, frozen once nobody is
watching, disabled when the Q&A was never wanted.
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
    from pytest_django.fixtures import SettingsWrapper


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

        A moderator with a stale tab polls every ten seconds; the switch has to take effect there
        too, not just for ordinary attendees.
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

        Every check reads ``talk.event``, so without select_related the list view would issue an
        extra query for each one.
        """
        talk = _talk_with_qa(Event.QAMode.OPEN)
        for i in range(10):
            baker.make(Question, talk=talk, content=f"Question {i}")
        client.force_login(_member_of(talk, "reader@example.com"))

        url = reverse("talk_questions", kwargs={"talk_id": talk.pk})
        with assert_no_n_plus_one():
            client.get(url)


@pytest.mark.django_db
class TestFrozenModeClosesEditing:
    """Freezing has to close the edit form too, not just the create form."""

    def test_the_author_cannot_edit_their_question(self, client: Client) -> None:
        """
        An edit replaces the body wholesale, so it is a way to post new content after a freeze.

        Without this the freeze is cosmetic for anyone who already has a question in the thread.
        """
        talk = _talk_with_qa(Event.QAMode.FROZEN)
        author = _member_of(talk, "author@example.com")
        question = baker.make(Question, talk=talk, user=author, content="An early question")
        client.force_login(author)

        response = client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Something else entirely"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.CONFLICT
        question.refresh_from_db()
        assert question.content == "An early question"

    def test_a_plain_post_is_refused_too(self, client: Client) -> None:
        """Without HTMX the refusal is a flash plus a redirect, but it still refuses."""
        talk = _talk_with_qa(Event.QAMode.FROZEN)
        author = _member_of(talk, "author@example.com")
        question = baker.make(Question, talk=talk, user=author, content="An early question")
        client.force_login(author)

        response = client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "Something else entirely"},
        )

        assert response.status_code == HTTPStatus.FOUND
        question.refresh_from_db()
        assert question.content == "An early question"

    def test_editing_stays_open_while_the_qa_is_open(self, client: Client) -> None:
        """The gate must not close editing on an event that is still taking questions."""
        talk = _talk_with_qa(Event.QAMode.OPEN)
        author = _member_of(talk, "author@example.com")
        question = baker.make(Question, talk=talk, user=author, content="An early question")
        client.force_login(author)

        response = client.post(
            reverse("question_edit", kwargs={"question_id": question.pk}),
            {"content": "A clearer question"},
        )

        assert response.status_code == HTTPStatus.FOUND
        question.refresh_from_db()
        assert question.content == "A clearer question"


@pytest.mark.django_db
class TestDisabledModeClosesEveryEndpoint:
    """Switching the Q&A off has to close the write endpoints, not only the list."""

    def test_voting_is_refused(self, client: Client) -> None:
        """A stale tab must not keep voting into a Q&A that no longer exists on the site."""
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        author = _member_of(talk, "author@example.com")
        question = baker.make(Question, talk=talk, user=author)
        client.force_login(_member_of(talk, "voter@example.com"))

        response = client.post(reverse("question_vote", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert question.votes.count() == 0

    def test_moderation_is_refused(self, client: Client) -> None:
        """``DISABLED`` yields nothing even to moderators, so there is nothing to moderate."""
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        author = _member_of(talk, "author@example.com")
        question = baker.make(Question, talk=talk, user=author, status=Question.Status.PENDING)
        client.force_login(_member_of(talk, "mod@example.com", staff=True))

        response = client.post(reverse("question_approve", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.NOT_FOUND
        question.refresh_from_db()
        assert question.status == Question.Status.PENDING

    def test_deleting_is_refused(self, client: Client) -> None:
        """Same for the author's own delete button."""
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        author = _member_of(talk, "author@example.com")
        question = baker.make(Question, talk=talk, user=author)
        client.force_login(author)

        response = client.post(reverse("question_delete", kwargs={"question_id": question.pk}))

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert Question.objects.filter(pk=question.pk).exists()

    def test_the_talk_list_does_not_link_to_it(self, client: Client) -> None:
        """A button that only ever 404s should not be rendered."""
        talk = _talk_with_qa(Event.QAMode.DISABLED)
        client.force_login(_member_of(talk, "browser@example.com"))

        response = client.get(reverse("talk_list"), {"event": "all"})

        assert reverse("talk_questions", kwargs={"talk_id": talk.pk}).encode() not in (
            response.content
        )


@pytest.mark.django_db
class TestParticipationNeedsAccessToTheEvent:
    """
    Reading a Q&A is open to anyone who can see the talk.

    Taking part is not.     Otherwise a ticket for last year's public archive was enough to post
    into the Q&A of the     conference running right now, which is where moderator attention is
    scarcest.
    """

    @staticmethod
    def _outsider() -> CustomUser:
        """Return a logged-in user holding a ticket for some other, unrelated event."""
        other = Event.objects.create(
            name="Last year",
            slug="last-year",
            visibility=Event.Visibility.PUBLIC,
        )
        user = baker.make(CustomUser, email="outsider@example.com")
        user.events.add(other)
        return user

    def test_an_outsider_cannot_post_on_a_schedule_only_event(self, client: Client) -> None:
        """The live-event case: browsable to everyone, but only ticket holders may ask."""
        event = Event.objects.create(
            name="This year",
            slug="this-year",
            visibility=Event.Visibility.SCHEDULE_ONLY,
        )
        talk = baker.make(Talk, event=event, title="A talk")
        client.force_login(self._outsider())

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "How does this work in practice?"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert not Question.objects.filter(talk=talk).exists()

    def test_an_outsider_cannot_vote_on_a_schedule_only_event(self, client: Client) -> None:
        """A vote orders the thread and the moderator queue, so it needs the same standing."""
        event = Event.objects.create(
            name="This year",
            slug="this-year",
            visibility=Event.Visibility.SCHEDULE_ONLY,
        )
        talk = baker.make(Talk, event=event, title="A talk")
        author = _member_of(talk, "author@example.com")
        question = baker.make(Question, talk=talk, user=author)
        client.force_login(self._outsider())

        response = client.post(
            reverse("question_vote", kwargs={"question_id": question.pk}),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.FORBIDDEN
        assert question.votes.count() == 0

    def test_an_outsider_can_still_read_the_thread(self, client: Client) -> None:
        """Reading stays open, and the page says why the form is missing."""
        event = Event.objects.create(
            name="This year",
            slug="this-year",
            visibility=Event.Visibility.SCHEDULE_ONLY,
        )
        talk = baker.make(Talk, event=event, title="A talk")
        author = _member_of(talk, "author@example.com")
        baker.make(Question, talk=talk, user=author, content="A published question")
        client.force_login(self._outsider())

        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))

        assert response.status_code == HTTPStatus.OK
        assert b"A published question" in response.content
        assert response.context["user_can_join_qa"] is False
        # No form offered, since submitting it would be refused.
        assert b'id="question-form"' not in response.content

    def test_a_ticket_holder_may_post(self, client: Client) -> None:
        """The ordinary case is untouched."""
        talk = _talk_with_qa(Event.QAMode.OPEN)
        client.force_login(_member_of(talk, "member@example.com"))

        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "A straightforward question"},
        )

        assert Question.objects.filter(talk=talk).exists()

    def test_anyone_with_an_account_may_post_on_a_public_event(self, client: Client) -> None:
        """
        A public event needs no ticket, because registration for it needs no ticket either.

        Requiring one here would only mean "whoever happened to register through this event", which
        protects nothing while breaking Q&A for everyone who arrived another way.
        """
        event = Event.objects.create(
            name="Archive",
            slug="archive",
            visibility=Event.Visibility.PUBLIC,
        )
        talk = baker.make(Talk, event=event, title="A talk")
        client.force_login(baker.make(CustomUser, email="nobody@example.com"))

        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Was this recorded on the day?"},
        )

        assert Question.objects.filter(talk=talk).exists()

    def test_a_moderator_may_post_without_a_ticket(self, client: Client) -> None:
        """Moderators are the people working the queue, whatever they hold a ticket for."""
        event = Event.objects.create(
            name="This year",
            slug="this-year",
            visibility=Event.Visibility.SCHEDULE_ONLY,
        )
        talk = baker.make(Talk, event=event, title="A talk")
        client.force_login(baker.make(CustomUser, email="mod@example.com", is_staff=True))

        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Seeding a question from the stage"},
        )

        assert Question.objects.filter(talk=talk).exists()


@pytest.mark.django_db
class TestCreateFormGet:
    """A GET on the create endpoint is a navigation mistake, not a server error."""

    def test_get_redirects_to_the_page_that_has_the_form(self, client: Client) -> None:
        """
        The form is embedded in the question list, so there is no standalone template.

        ``template_name`` used to name one that does not exist, making a GET here a 500.
        """
        talk = _talk_with_qa(Event.QAMode.OPEN)
        client.force_login(_member_of(talk, "member@example.com"))

        response = client.get(reverse("question_create", kwargs={"talk_id": talk.pk}))

        assert response.status_code == HTTPStatus.FOUND
        assert response.headers["Location"] == reverse(
            "talk_questions",
            kwargs={"talk_id": talk.pk},
        )

    def test_a_get_does_not_spend_the_rate_limit(
        self,
        client: Client,
        settings: SettingsWrapper,
    ) -> None:
        """
        Opening the page must not count against the allowance.

        The limit is claimed rather than peeked at now, so a GET that reached the claim would
        quietly burn a question every time someone loaded the form.
        """
        settings.QA_QUESTION_RATE_LIMIT_PER_TALK = 1
        talk = _talk_with_qa(Event.QAMode.OPEN)
        client.force_login(_member_of(talk, "member@example.com"))

        for _ in range(3):
            client.get(reverse("question_create", kwargs={"talk_id": talk.pk}))

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Still allowed to ask"},
        )

        assert response.status_code == HTTPStatus.FOUND
        assert Question.objects.filter(talk=talk).count() == 1


@pytest.mark.django_db
class TestQaErrorsAreDeliveredSafely:
    """
    A Q&A error has to be identifiable, or the swap opt-in cannot be scoped.

    ``base.html`` keys its 4xx swap on the header these responses carry. Keyed on the status code
    instead, every HTMX control on the site would swap whatever a 4xx returned - Django's 404 page
    into a bookmark button, the CSRF failure page into the rating widget.
    """

    def test_an_error_carries_the_swap_and_placement_headers(self, client: Client) -> None:
        """
        The marker plus the three placement headers, which are one contract.

        Retarget/reswap/reselect matter because the moderation and vote buttons all target
        ``#question-list`` with ``outerHTML`` and inherit ``hx-select`` from the fragment root:
        without redirecting all three, the error body is filtered to nothing and the swap deletes
        the whole thread and its poller.
        """
        talk = _talk_with_qa(Event.QAMode.FROZEN)
        client.force_login(_member_of(talk, "asker@example.com"))

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Too late"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.CONFLICT
        assert response.headers["HX-Qa-Error"] == "1"
        assert response.headers["HX-Retarget"] == "#question-error"
        assert response.headers["HX-Reswap"] == "innerHTML"
        assert response.headers["HX-Reselect"] == "#qa-error-body"
        # The id HX-Reselect names has to exist in the body it is selecting from.
        assert b'id="qa-error-body"' in response.content

    def test_other_endpoints_do_not_get_the_swap_marker(self, client: Client) -> None:
        """
        A 4xx from anything else must not be swapped, which is htmx's own default.

        This is the whole point of the header: the bookmark toggle answers a missing talk with
        Django's full 404 page, which would otherwise be pasted into a small button.
        """
        talk = _talk_with_qa(Event.QAMode.OPEN)
        client.force_login(_member_of(talk, "member@example.com"))

        response = client.post(
            reverse("toggle_save_talk", kwargs={"talk_id": 999999}),
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.NOT_FOUND
        assert "HX-Qa-Error" not in response.headers

    def test_the_page_has_the_region_the_errors_are_sent_to(self, client: Client) -> None:
        """The retarget names an element, so the page has to actually contain it."""
        talk = _talk_with_qa(Event.QAMode.OPEN)
        client.force_login(_member_of(talk, "reader@example.com"))

        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))

        assert b'id="question-error"' in response.content

    def test_a_non_htmx_error_is_shown_after_the_redirect(self, client: Client) -> None:
        """
        Without JavaScript the error is flashed and the visitor redirected back.

        Nothing in this render path iterated ``messages`` before, so a question refused without
        JavaScript disappeared with no explanation whatsoever.
        """
        talk = _talk_with_qa(Event.QAMode.FROZEN)
        client.force_login(_member_of(talk, "asker@example.com"))

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "Too late"},
            follow=True,
        )

        assert response.status_code == HTTPStatus.OK
        assert b"Questions are closed for this talk" in response.content
