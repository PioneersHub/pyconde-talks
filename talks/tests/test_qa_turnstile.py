"""
Tests for the Turnstile challenge on the question form.

The important property is that a deployment without keys is completely unaffected: no field,
no verification call, no behaviour change. Everything else is what happens once it is on.
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
    import respx
    from django.test import Client
    from pytest_django.fixtures import SettingsWrapper


VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

pytestmark = pytest.mark.httpx2(assert_all_called=False)


@pytest.fixture
def talk() -> Talk:
    """Return a talk on an event with an open Q&A."""
    event = Event.objects.create(name="Event", slug="event")
    return baker.make(Talk, event=event, title="A talk")


@pytest.fixture
def asker(talk: Talk) -> CustomUser:
    """Return a user with access to the talk's event."""
    user = baker.make(CustomUser, email="asker@example.com")
    user.events.add(talk.event)
    return user


@pytest.fixture
def _turnstile_on(settings: SettingsWrapper) -> None:
    """Switch the captcha on with test keys."""
    settings.TURNSTILE_SITE_KEY = "site-key"
    settings.TURNSTILE_SECRET_KEY = "secret-key"
    settings.TURNSTILE_VERIFY_URL = VERIFY_URL
    settings.TURNSTILE_TIMEOUT = 5
    settings.TURNSTILE_FAIL_OPEN = True


@pytest.mark.django_db
class TestTurnstileDisabled:
    """The default. Nothing about the form changes."""

    def test_the_widget_is_absent(self, client: Client, talk: Talk, asker: CustomUser) -> None:
        """No captcha markup and no Cloudflare script on the page."""
        client.force_login(asker)
        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))

        assert b"cf-turnstile" not in response.content
        assert b"challenges.cloudflare.com" not in response.content

    def test_a_question_posts_without_a_token(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        httpx2_mock: respx.Router,
    ) -> None:
        """Submitting works with no token, and Cloudflare is never contacted."""
        route = httpx2_mock.post(VERIFY_URL)
        client.force_login(asker)

        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "A question"},
            HTTP_HX_REQUEST="true",
        )

        assert Question.objects.filter(talk=talk).count() == 1
        assert route.called is False


@pytest.mark.django_db
@pytest.mark.usefixtures("_turnstile_on")
class TestTurnstileEnabled:
    """Once configured, the challenge has to pass before a question is stored."""

    def test_the_widget_is_rendered(self, client: Client, talk: Talk, asker: CustomUser) -> None:
        """The container and the Cloudflare script appear, carrying the site key."""
        client.force_login(asker)
        response = client.get(reverse("talk_questions", kwargs={"talk_id": talk.pk}))
        body = response.content.decode()

        assert 'class="cf-turnstile"' in body
        assert 'data-sitekey="site-key"' in body
        assert "challenges.cloudflare.com" in body

    def test_a_valid_token_is_accepted(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        httpx2_mock: respx.Router,
    ) -> None:
        """The ordinary path: challenge solved, question stored."""
        httpx2_mock.post(VERIFY_URL).respond(json={"success": True})
        client.force_login(asker)

        client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "A question", "cf-turnstile-response": "a-token"},
            HTTP_HX_REQUEST="true",
        )

        assert Question.objects.filter(talk=talk).count() == 1

    def test_a_missing_token_is_rejected(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
    ) -> None:
        """A POST that skips the widget entirely gets a readable error, not a 500."""
        client.force_login(asker)

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "A question"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert b"anti-spam check" in response.content
        assert Question.objects.filter(talk=talk).count() == 0

    def test_a_rejected_token_is_refused(
        self,
        client: Client,
        talk: Talk,
        asker: CustomUser,
        httpx2_mock: respx.Router,
    ) -> None:
        """Cloudflare says no, so the question is not stored."""
        httpx2_mock.post(VERIFY_URL).respond(
            json={"success": False, "error-codes": ["invalid-input-response"]},
        )
        client.force_login(asker)

        response = client.post(
            reverse("question_create", kwargs={"talk_id": talk.pk}),
            {"content": "A question", "cf-turnstile-response": "a-bad-token"},
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert b"did not pass" in response.content
        assert Question.objects.filter(talk=talk).count() == 0
