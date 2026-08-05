"""Tests for internationalization: the language switcher, persistence, middleware, and emails."""

# Non-English strings asserted in the tests below are intentional, not typos.
# cspell:ignore código acesso entrar direitos reservados Alle Rechte vorbehalten Todos los derechos
# cspell:ignore msgids

from http import HTTPStatus
from pathlib import Path
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


@pytest.fixture
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
# (language cookie, expected <html lang> value, an always-present translated footer string)
LANGUAGE_RENDER_CASES = [
    ("en", "en", "All rights reserved"),
    ("pt-br", "pt-br", "Todos os direitos reservados"),
    ("de", "de", "Alle Rechte vorbehalten"),
    ("es", "es", "Todos los derechos reservados"),
]


@pytest.mark.django_db
class TestRendering:
    """Pages render in the language selected via the cookie."""

    @pytest.mark.parametrize(("cookie", "lang_attr", "footer"), LANGUAGE_RENDER_CASES)
    def test_login_page_renders_in_selected_language(
        self,
        client: Client,
        cookie: str,
        lang_attr: str,
        footer: str,
    ) -> None:
        """The login page (login_not_required) renders translated chrome per the cookie."""
        client.cookies[LANGUAGE_COOKIE] = cookie
        response = client.get(reverse("account_login"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert f'<html lang="{lang_attr}">' in content
        assert footer in content

    def test_login_page_renders_in_english_by_default(self, client: Client) -> None:
        """Without a language cookie the default English chrome is served."""
        response = client.get(reverse("account_login"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert '<html lang="en">' in content
        assert "All rights reserved" in content


# --------------------------------------------------------------------------------------------------
# Cross-test isolation
# --------------------------------------------------------------------------------------------------
class TestLanguageIsolation:
    """
    The autouse ``_reset_active_language`` fixture in the root conftest.

    ``LocaleMiddleware`` activates a language and never restores it, so without the fixture the
    tests above would leave German (or Portuguese) active for whatever runs next.
    """

    @pytest.mark.parametrize("_run", [1, 2])
    def test_activated_language_does_not_leak_between_tests(self, _run: int) -> None:
        """Both runs activate German, and both still start on the default language."""
        assert translation.get_language() == settings.LANGUAGE_CODE
        translation.activate("de")


# --------------------------------------------------------------------------------------------------
# Catalogue completeness
# --------------------------------------------------------------------------------------------------
def _catalog_path(language_code: str) -> Path:
    """Return the .po path for an offered language code (``pt-br`` -> ``pt_BR``)."""
    if "-" in language_code:
        base, region = language_code.split("-", 1)
        locale_name = f"{base}_{region.upper()}"
    else:
        locale_name = language_code
    return Path(settings.LOCALE_PATHS[0]) / locale_name / "LC_MESSAGES" / "django.po"


def _read_po_entry(block: str) -> tuple[str, bool, bool]:
    """
    Return ``(msgid, is_fuzzy, is_translated)`` for one blank-line-separated .po entry.

    A minimal reader rather than a dependency. ``msgid`` and ``msgstr`` values can be split over
    several quoted lines, so each continuation line is appended to whichever key was last seen;
    ``msgid_plural`` is skipped because its text is not what identifies the entry.
    """
    msgid_parts: list[str] = []
    msgstr_parts: list[str] = []
    fuzzy = False
    target: list[str] | None = None

    for line in block.splitlines():
        if line.startswith("#,") and "fuzzy" in line:
            fuzzy = True
        elif line.startswith("msgid_plural "):
            target = None
        elif line.startswith("msgid "):
            target = msgid_parts
            msgid_parts.append(line.removeprefix("msgid ").strip())
        elif line.startswith("msgstr"):
            target = msgstr_parts
            msgstr_parts.append(line.split(" ", 1)[1].strip() if " " in line else '""')
        elif line.startswith('"') and target is not None:
            target.append(line.strip())
        elif not line.startswith(("#", '"')):
            target = None

    msgid = "".join(part[1:-1] for part in msgid_parts)
    translated = any(part.strip('"') for part in msgstr_parts)
    return msgid, fuzzy, translated


def _entries_missing_a_translation(po_text: str) -> list[str]:
    """
    Return the msgids in *po_text* that are untranslated or still marked fuzzy.

    A translation counts as missing when every ``msgstr`` literal it has is empty, or when the
    entry carries the ``fuzzy`` flag: gettext ignores fuzzy entries at runtime, so they render in
    English exactly as if they were absent.
    """
    missing = []
    for block in po_text.split("\n\n"):
        msgid, fuzzy, translated = _read_po_entry(block)
        if not msgid:
            # The header entry, which has an empty msgid.
            continue
        if fuzzy or not translated:
            missing.append(msgid)
    return missing


@pytest.mark.parametrize(
    "language_code",
    [code for code, _label in settings.LANGUAGES if code != settings.LANGUAGE_CODE],
)
def test_every_offered_language_is_fully_translated(language_code: str) -> None:
    """
    Every language in the switcher has a translation for every string the site ships.

    Without this the catalogues rot silently: a feature adds thirty strings, nobody runs
    ``makemessages``, and German visitors get English text in the middle of a German page with
    nothing failing anywhere. Fuzzy entries count as missing, because gettext ignores them at
    runtime and serves the English source.

    If this fails, run ``makemessages`` for the locale, translate the new ``msgstr`` entries, then
    ``compilemessages``. See docs/development/translations.md.
    """
    path = _catalog_path(language_code)
    assert path.exists(), f"no catalogue for {language_code} at {path}"

    missing = _entries_missing_a_translation(path.read_text(encoding="utf-8"))

    assert not missing, (
        f"{language_code}: {len(missing)} string(s) untranslated or fuzzy, e.g. {missing[:5]}"
    )


@pytest.mark.parametrize(
    "language_code",
    [code for code, _label in settings.LANGUAGES if code != settings.LANGUAGE_CODE],
)
def test_every_catalogue_is_compiled_and_current(language_code: str) -> None:
    """
    The compiled ``.mo`` exists and is not older than its ``.po``.

    Django reads the ``.mo``, so editing the ``.po`` without running ``compilemessages`` changes
    nothing at runtime while looking done in review. Both files are committed.
    """
    po = _catalog_path(language_code)
    mo = po.with_suffix(".mo")

    assert mo.exists(), f"{language_code}: {mo} missing, run compilemessages"
    assert mo.stat().st_mtime >= po.stat().st_mtime, (
        f"{language_code}: {mo.name} is older than {po.name}, run compilemessages"
    )
