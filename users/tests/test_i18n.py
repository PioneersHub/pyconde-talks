"""Tests for internationalization: the language switcher, persistence, middleware, and emails."""

# Portuguese strings asserted in the tests below are intentional, not typos.
# cspell:ignore código acesso entrar direitos reservados

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from allauth.account.adapter import get_adapter
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation

from users.middleware import UserLanguageMiddleware
from users.models import CustomUser


if TYPE_CHECKING:
    from django.core.mail import EmailMessage
    from django.test.client import Client


LANGUAGE_COOKIE = settings.LANGUAGE_COOKIE_NAME


@pytest.fixture()
def user(db: None) -> CustomUser:
    """Create a regular (passwordless) user."""
    return CustomUser.objects.create_user(email="attendee@example.com", is_active=True)


# --------------------------------------------------------------------------------------------------
# set_language view
# --------------------------------------------------------------------------------------------------
@pytest.mark.django_db
class TestSetLanguageView:
    """
    The custom set_language wrapper view.

    Marked django_db because ATOMIC_REQUESTS wraps every request (even anonymous GETs) in a
    transaction, so any call through the test client touches the database.
    """

    def test_anonymous_can_switch_and_cookie_is_set(self, client: Client) -> None:
        """Anonymous visitors (login_not_required) get the language cookie, no login redirect."""
        response = client.post(
            reverse("set_language"),
            {"language": "pt-br", "next": "/"},
        )
        assert response.status_code == HTTPStatus.FOUND
        assert response["Location"] == "/"
        assert client.cookies[LANGUAGE_COOKIE].value == "pt-br"

    def test_get_is_not_allowed(self, client: Client) -> None:
        """The view only accepts POST (state-changing)."""
        response = client.get(reverse("set_language"))
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    def test_authenticated_choice_is_persisted_on_profile(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """A logged-in user's choice is saved to preferred_language and the cookie."""
        client.force_login(user)
        response = client.post(
            reverse("set_language"),
            {"language": "pt-br", "next": "/"},
        )
        assert response.status_code == HTTPStatus.FOUND
        user.refresh_from_db()
        assert user.preferred_language == "pt-br"
        assert client.cookies[LANGUAGE_COOKIE].value == "pt-br"

    def test_unoffered_language_is_not_persisted(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """A language outside settings.LANGUAGES is never written to the profile."""
        client.force_login(user)
        client.post(reverse("set_language"), {"language": "xx-yy", "next": "/"})
        user.refresh_from_db()
        assert user.preferred_language == ""

    def test_switching_back_updates_profile(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """Switching to a second language overwrites the stored preference."""
        user.preferred_language = "pt-br"
        user.save(update_fields=["preferred_language"])
        client.force_login(user)
        client.post(reverse("set_language"), {"language": "en", "next": "/"})
        user.refresh_from_db()
        assert user.preferred_language == "en"


# --------------------------------------------------------------------------------------------------
# UserLanguageMiddleware
# --------------------------------------------------------------------------------------------------
class TestUserLanguageMiddleware:
    """The middleware that applies a logged-in user's saved language per request."""

    def _run(self, request: HttpRequest) -> str:
        """Run the middleware and capture the language active while the view runs."""
        captured: dict[str, str] = {}

        def get_response(_request: HttpRequest) -> HttpResponse:
            captured["language"] = translation.get_language() or ""
            return HttpResponse("ok")

        UserLanguageMiddleware(get_response)(request)
        return captured["language"]

    def test_preferred_language_is_activated(self, db: None, user: CustomUser) -> None:
        """An authenticated user's preference overrides the ambient language."""
        user.preferred_language = "pt-br"
        request = RequestFactory().get("/")
        request.user = user
        with translation.override("en"):
            assert self._run(request) == "pt-br"

    def test_blank_preference_leaves_language_untouched(
        self,
        db: None,
        user: CustomUser,
    ) -> None:
        """A user with no preference keeps whatever LocaleMiddleware resolved."""
        request = RequestFactory().get("/")
        request.user = user  # preferred_language == ""
        with translation.override("en"):
            assert self._run(request) == "en"

    def test_anonymous_user_is_ignored(self) -> None:
        """Anonymous users have no preference attribute; the language is left as-is."""
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        with translation.override("en"):
            assert self._run(request) == "en"


# --------------------------------------------------------------------------------------------------
# Transactional email language
# --------------------------------------------------------------------------------------------------
class TestEmailLanguage:
    """Login-code emails are rendered in the recipient's saved language."""

    def test_login_code_email_uses_preferred_language(
        self,
        user: CustomUser,
        mailoutbox: list[EmailMessage],
    ) -> None:
        """A pt-br user receives the login code email in Brazilian Portuguese."""
        user.preferred_language = "pt-br"
        user.save(update_fields=["preferred_language"])

        with translation.override("en"):
            get_adapter().send_mail(
                "account/email/login_code",
                user.email,
                {"code": "ABC123"},
            )

        assert len(mailoutbox) == 1
        assert "código de acesso" in mailoutbox[0].body

    def test_login_code_email_falls_back_to_active_language(
        self,
        user: CustomUser,
        mailoutbox: list[EmailMessage],
    ) -> None:
        """With no stored preference, the email follows the request's active language."""
        with translation.override("en"):
            get_adapter().send_mail(
                "account/email/login_code",
                user.email,
                {"code": "ABC123"},
            )

        assert len(mailoutbox) == 1
        assert "login code" in mailoutbox[0].body.lower()


# --------------------------------------------------------------------------------------------------
# End-to-end rendering
# --------------------------------------------------------------------------------------------------
@pytest.mark.django_db
class TestRendering:
    """Pages render in the language selected via the cookie."""

    def test_login_page_renders_in_portuguese(self, client: Client) -> None:
        """The login page (login_not_required) renders translated chrome when cookie is pt-br."""
        client.cookies[LANGUAGE_COOKIE] = "pt-br"
        response = client.get(reverse("account_login"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert '<html lang="pt-br">' in content
        # "Sign In" -> "Entrar" and the always-present footer line.
        assert "Todos os direitos reservados" in content

    def test_login_page_renders_in_english_by_default(self, client: Client) -> None:
        """Without a language cookie the default English chrome is served."""
        response = client.get(reverse("account_login"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert '<html lang="en">' in content
        assert "All rights reserved" in content
