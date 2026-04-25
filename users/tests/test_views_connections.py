"""Tests for the add-email and connections views."""

# cspell:words RPQJ NBKC TKLM BVWZ rpqj nbkc

from datetime import timedelta
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest
from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.core import mail as django_mail
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from users.adapters import AccountAdapter
from users.adapters_social import SocialAccountAdapter
from users.models import CustomUser
from users.views_connections import _ADD_EMAIL_SESSION_KEY


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def discord_user() -> CustomUser:
    """Return a user who signed up via Discord (no verified email, no password)."""
    user = baker.make(CustomUser, email="discord-user@example.com")
    user.set_unusable_password()
    user.save()
    return user


@pytest.fixture()
def email_user() -> CustomUser:
    """Return a user who signed up via email (has verified email, no password)."""
    user = baker.make(CustomUser, email="email-user@example.com")
    user.set_unusable_password()
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


@pytest.fixture()
def discord_social_account(discord_user: CustomUser) -> SocialAccount:
    """Link a Discord social account to the discord_user."""
    return SocialAccount.objects.create(
        user=discord_user,
        provider="discord",
        uid="123456789",
        extra_data={"username": "testuser", "email": "discord-user@example.com"},
    )


@pytest.fixture()
def discord_user_with_verified_email(
    discord_user: CustomUser,
    discord_social_account: SocialAccount,
) -> CustomUser:
    """Discord user with a verified EmailAddress (created by allauth on Discord login)."""
    EmailAddress.objects.create(
        user=discord_user,
        email=discord_user.email,
        verified=True,
        primary=True,
    )
    return discord_user


@pytest.fixture()
def email_user_with_discord(email_user: CustomUser) -> SocialAccount:
    """Link a Discord social account to the email_user (has both login methods)."""
    return SocialAccount.objects.create(
        user=email_user,
        provider="discord",
        uid="987654321",
        extra_data={"username": "email-user", "email": "email-user@example.com"},
    )


# ---------------------------------------------------------------------------
# Connections view
# ---------------------------------------------------------------------------


class TestConnectionsView:
    """Tests for the custom connections_view wrapper."""

    def test_discord_only_user_cannot_disconnect(
        self,
        client: Any,
        discord_user: CustomUser,
        discord_social_account: SocialAccount,
    ) -> None:
        """Discord-only user should not see the remove button."""
        client.force_login(discord_user)
        response = client.get(reverse("socialaccount_connections"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Remove selected" not in content
        assert "only sign-in method" in content

    @patch("users.views_connections.get_adapter")
    def test_email_user_with_discord_can_disconnect(
        self,
        mock_get_adapter: Any,
        client: Any,
        email_user: CustomUser,
        email_user_with_discord: SocialAccount,
    ) -> None:
        """User with API-authorized email should see the remove button."""
        mock_get_adapter.return_value.can_login_by_email.return_value = True
        client.force_login(email_user)
        response = client.get(reverse("socialaccount_connections"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Remove selected" in content
        assert "only sign-in method" not in content

    @patch("users.views_connections.get_adapter")
    def test_verified_email_not_in_api_blocks_disconnect(
        self,
        mock_get_adapter: Any,
        client: Any,
        email_user: CustomUser,
        email_user_with_discord: SocialAccount,
    ) -> None:
        """User with verified email NOT in validation API should not disconnect."""
        mock_get_adapter.return_value.can_login_by_email.return_value = False
        client.force_login(email_user)
        response = client.get(reverse("socialaccount_connections"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Remove selected" not in content
        assert "not authorized" in content
        # The actual email should be shown in the blocked message
        assert email_user.email in content

    def test_discord_only_user_sees_add_email(
        self,
        client: Any,
        discord_user: CustomUser,
        discord_social_account: SocialAccount,
    ) -> None:
        """Discord-only user should see the add-email card."""
        client.force_login(discord_user)
        response = client.get(reverse("socialaccount_connections"))
        content = response.content.decode()
        assert "Add Email" in content

    @patch("users.views_connections.get_adapter")
    def test_email_user_does_not_see_add_email(
        self,
        mock_get_adapter: Any,
        client: Any,
        email_user: CustomUser,
        email_user_with_discord: SocialAccount,
    ) -> None:
        """User with authorized verified email should not see the add-email card."""
        mock_get_adapter.return_value.can_login_by_email.return_value = True
        client.force_login(email_user)
        response = client.get(reverse("socialaccount_connections"))
        content = response.content.decode()
        assert reverse("add_email") not in content

    @patch("users.views_connections.get_adapter")
    def test_discord_user_unauthorized_email_sees_ticket_email_card(
        self,
        mock_get_adapter: Any,
        client: Any,
        discord_user_with_verified_email: CustomUser,
    ) -> None:
        """Discord user whose email is not in validation API sees 'Connect ticket email'."""
        mock_get_adapter.return_value.can_login_by_email.return_value = False
        client.force_login(discord_user_with_verified_email)
        response = client.get(reverse("socialaccount_connections"))
        content = response.content.decode()
        assert "Connect Ticket Email" in content
        assert reverse("add_email") in content

    def test_discord_user_does_not_see_link_discord_text(
        self,
        client: Any,
        discord_user: CustomUser,
        discord_social_account: SocialAccount,
    ) -> None:
        """User with Discord linked should not see 'Link your Discord account' text."""
        client.force_login(discord_user)
        response = client.get(reverse("socialaccount_connections"))
        content = response.content.decode()
        assert "Link your Discord" not in content

    def test_email_user_sees_link_discord_text(
        self,
        client: Any,
        email_user: CustomUser,
    ) -> None:
        """User without Discord should see 'Link your Discord account' text."""
        client.force_login(email_user)
        response = client.get(reverse("socialaccount_connections"))
        content = response.content.decode()
        assert "Link your Discord" in content

    @patch("users.views_connections.get_adapter")
    def test_discord_user_with_authorized_primary_can_disconnect(
        self,
        mock_get_adapter: Any,
        client: Any,
        discord_user_with_verified_email: CustomUser,
    ) -> None:
        """After connecting a ticket email, primary email is authorized - can disconnect."""
        user = discord_user_with_verified_email
        # Old Discord email (non-primary, still verified)
        old = EmailAddress.objects.get(user=user, email=user.email)
        old.primary = False
        old.save()
        # New authorized ticket email
        EmailAddress.objects.create(
            user=user,
            email="ticket@example.com",
            verified=True,
            primary=True,
        )
        user.email = "ticket@example.com"
        user.save()

        mock_get_adapter.return_value.can_login_by_email.return_value = True
        client.force_login(user)
        response = client.get(reverse("socialaccount_connections"))
        content = response.content.decode()
        assert "Remove selected" in content
        assert "Connect Ticket Email" not in content

    def test_unauthenticated_redirect(self, client: Any) -> None:
        """Unauthenticated users are redirected."""
        response = client.get(reverse("socialaccount_connections"))
        assert response.status_code == HTTPStatus.FOUND


# ---------------------------------------------------------------------------
# Disconnect protection (allauth's built-in + custom error message)
# ---------------------------------------------------------------------------


class TestDisconnectProtection:
    """Verify that allauth prevents disconnecting Discord for passwordless users."""

    def test_disconnect_blocked_no_password(
        self,
        client: Any,
        discord_user: CustomUser,
        discord_social_account: SocialAccount,
    ) -> None:
        """POST to disconnect should fail for a user with no password and no verified email."""
        client.force_login(discord_user)
        response = client.post(
            reverse("socialaccount_connections"),
            {"account": discord_social_account.pk},
        )
        # The form should re-render with an error (not actually disconnect)
        assert response.status_code == HTTPStatus.OK
        # Account should still exist
        assert SocialAccount.objects.filter(pk=discord_social_account.pk).exists()

    @patch("users.forms.get_account_adapter")
    def test_disconnect_allowed_with_api_authorized_email(
        self,
        mock_adapter: Any,
        client: Any,
        email_user: CustomUser,
        email_user_with_discord: SocialAccount,
    ) -> None:
        """POST to disconnect should succeed when email passes validation API."""
        mock_adapter.return_value.can_login_by_email.return_value = True
        client.force_login(email_user)
        response = client.post(
            reverse("socialaccount_connections"),
            {"account": email_user_with_discord.pk},
        )
        assert response.status_code == HTTPStatus.FOUND
        assert not SocialAccount.objects.filter(pk=email_user_with_discord.pk).exists()

    @patch("users.forms.get_account_adapter")
    def test_disconnect_blocked_email_not_in_api(
        self,
        mock_adapter: Any,
        client: Any,
        email_user: CustomUser,
        email_user_with_discord: SocialAccount,
    ) -> None:
        """POST to disconnect should fail when email does NOT pass validation API."""
        mock_adapter.return_value.can_login_by_email.return_value = False
        client.force_login(email_user)
        response = client.post(
            reverse("socialaccount_connections"),
            {"account": email_user_with_discord.pk},
        )
        assert response.status_code == HTTPStatus.OK
        assert SocialAccount.objects.filter(pk=email_user_with_discord.pk).exists()

    @patch("users.forms.get_account_adapter")
    def test_disconnect_works_after_ticket_email_change(
        self,
        mock_adapter: Any,
        client: Any,
        discord_user_with_verified_email: CustomUser,
    ) -> None:
        """Disconnect should succeed when primary email is now authorized."""
        user = discord_user_with_verified_email
        sa = SocialAccount.objects.get(user=user)
        # Old Discord email demoted, new ticket email is primary
        old = EmailAddress.objects.get(user=user, email=user.email)
        old.primary = False
        old.save()
        EmailAddress.objects.create(
            user=user,
            email="ticket@example.com",
            verified=True,
            primary=True,
        )
        user.email = "ticket@example.com"
        user.save()

        mock_adapter.return_value.can_login_by_email.return_value = True
        client.force_login(user)
        response = client.post(
            reverse("socialaccount_connections"),
            {"account": sa.pk},
        )
        assert response.status_code == HTTPStatus.FOUND
        assert not SocialAccount.objects.filter(pk=sa.pk).exists()


# ---------------------------------------------------------------------------
# Add email view
# ---------------------------------------------------------------------------


class TestAddEmailView:
    """Tests for add_email_view (step 1)."""

    def test_get_renders_form(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """GET should render the add-email form."""
        client.force_login(discord_user)
        response = client.get(reverse("add_email"))
        assert response.status_code == HTTPStatus.OK
        assert b"Add Email Address" in response.content

    @patch("users.views_connections.get_adapter")
    def test_redirect_if_already_has_authorized_email(
        self,
        mock_get_adapter: Any,
        client: Any,
        email_user: CustomUser,
    ) -> None:
        """Users with a verified and authorized email should be redirected."""
        mock_get_adapter.return_value.can_login_by_email.return_value = True
        client.force_login(email_user)
        response = client.get(reverse("add_email"))
        assert response.status_code == HTTPStatus.FOUND
        assert reverse("socialaccount_connections") in response.url

    @patch("users.views_connections.get_adapter")
    def test_discord_user_unauthorized_email_sees_form(
        self,
        mock_get_adapter: Any,
        client: Any,
        discord_user_with_verified_email: CustomUser,
    ) -> None:
        """Discord user whose email is NOT in validation API should see the add-email form."""
        mock_get_adapter.return_value.can_login_by_email.return_value = False
        client.force_login(discord_user_with_verified_email)
        response = client.get(reverse("add_email"))
        assert response.status_code == HTTPStatus.OK
        assert b"Add Email Address" in response.content

    def test_invalid_email_format(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Invalid email format should re-render with error."""
        client.force_login(discord_user)
        response = client.post(reverse("add_email"), {"email": "not-an-email"})
        assert response.status_code == HTTPStatus.OK
        assert b"valid email" in response.content

    def test_email_taken_by_other_user(
        self,
        client: Any,
        discord_user: CustomUser,
        email_user: CustomUser,
    ) -> None:
        """Email already used by another user should be rejected."""
        client.force_login(discord_user)
        response = client.post(reverse("add_email"), {"email": email_user.email})
        assert response.status_code == HTTPStatus.OK
        assert b"already in use" in response.content

    @patch("users.views_connections.get_adapter")
    @patch("users.views_connections._send_add_email_code")
    def test_unauthorized_email_rejected(
        self,
        mock_send: Any,
        mock_get_adapter: Any,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Email not authorized by the API should be rejected."""
        mock_adapter = mock_get_adapter.return_value
        mock_adapter.is_email_authorized.return_value = False
        client.force_login(discord_user)
        response = client.post(reverse("add_email"), {"email": "new@example.com"})
        assert response.status_code == HTTPStatus.OK
        assert b"not authorized" in response.content
        mock_send.assert_not_called()

    @patch("users.views_connections.get_adapter")
    @patch("users.views_connections._send_add_email_code")
    def test_authorized_email_sends_code(
        self,
        mock_send: Any,
        mock_get_adapter: Any,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Authorized email should store session data and send code."""
        mock_adapter = mock_get_adapter.return_value
        mock_adapter.is_email_authorized.return_value = True
        client.force_login(discord_user)
        response = client.post(reverse("add_email"), {"email": "new@example.com"})
        assert response.status_code == HTTPStatus.FOUND
        assert reverse("confirm_add_email") in response.url
        mock_send.assert_called_once()
        # Verify session data was stored
        session = client.session
        assert _ADD_EMAIL_SESSION_KEY in session
        assert session[_ADD_EMAIL_SESSION_KEY]["email"] == "new@example.com"

    @patch("users.views_connections.get_adapter")
    def test_add_email_code_sends_html_email(
        self,
        mock_get_adapter: Any,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """The verification code email should include an HTML body with branding."""
        mock_adapter = mock_get_adapter.return_value
        mock_adapter.is_email_authorized.return_value = True
        client.force_login(discord_user)
        client.post(reverse("add_email"), {"email": "new@example.com"})
        assert len(django_mail.outbox) == 1
        msg = django_mail.outbox[0]
        assert hasattr(msg, "alternatives")
        html_content, mime = msg.alternatives[0]  # type: ignore[union-attr]
        assert mime == "text/html"
        assert "verification code" in str(html_content).lower()
        assert "expire" in str(html_content).lower()

    def test_unauthenticated_redirect(self, client: Any) -> None:
        """Unauthenticated users are redirected."""
        response = client.get(reverse("add_email"))
        assert response.status_code == HTTPStatus.FOUND


# ---------------------------------------------------------------------------
# Confirm add email view
# ---------------------------------------------------------------------------


class TestConfirmAddEmailView:
    """Tests for confirm_add_email_view (step 2)."""

    def _set_session(
        self,
        client: Any,
        email: str = "new@example.com",
        code: str = "123456",
        expires_delta: timedelta | None = None,
    ) -> None:
        """Store a valid add-email session entry."""
        expires = timezone.now() + (expires_delta or timedelta(minutes=5))
        session = client.session
        session[_ADD_EMAIL_SESSION_KEY] = {
            "email": email,
            "code": code,
            "expires": expires.isoformat(),
            "attempts": 0,
            "event_slug": "",
        }
        session.save()

    def test_redirect_without_session(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Without session data, redirect to add_email."""
        client.force_login(discord_user)
        response = client.get(reverse("confirm_add_email"))
        assert response.status_code == HTTPStatus.FOUND
        assert reverse("add_email") in response.url

    def test_get_renders_form(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """GET with valid session should render the code form."""
        client.force_login(discord_user)
        self._set_session(client)
        response = client.get(reverse("confirm_add_email"))
        assert response.status_code == HTTPStatus.OK
        assert b"Verification Code" in response.content

    def test_correct_code_creates_email(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Correct code should create a verified EmailAddress and redirect."""
        client.force_login(discord_user)
        self._set_session(client, code="RPQJ-NBKC")
        response = client.post(reverse("confirm_add_email"), {"code": "RPQJ-NBKC"})
        assert response.status_code == HTTPStatus.FOUND
        assert reverse("socialaccount_connections") in response.url
        # Verify EmailAddress was created
        assert EmailAddress.objects.filter(
            user=discord_user,
            email="new@example.com",
            verified=True,
        ).exists()

    def test_code_is_case_insensitive(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Code comparison should be case-insensitive."""
        client.force_login(discord_user)
        self._set_session(client, code="RPQJ-NBKC")
        response = client.post(reverse("confirm_add_email"), {"code": "rpqj-nbkc"})
        assert response.status_code == HTTPStatus.FOUND
        assert EmailAddress.objects.filter(
            user=discord_user,
            email="new@example.com",
            verified=True,
        ).exists()

    def test_incorrect_code_shows_error(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Wrong code should re-render with error."""
        client.force_login(discord_user)
        self._set_session(client, code="RPQJ-NBKC")
        response = client.post(reverse("confirm_add_email"), {"code": "XXXX-YYYY"})
        assert response.status_code == HTTPStatus.OK
        assert b"incorrect" in response.content

    def test_expired_code_redirects(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Expired code should redirect back to add_email."""
        client.force_login(discord_user)
        self._set_session(client, expires_delta=timedelta(minutes=-1))
        response = client.post(reverse("confirm_add_email"), {"code": "123456"})
        assert response.status_code == HTTPStatus.FOUND
        assert reverse("add_email") in response.url

    def test_max_attempts_exceeded(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """Exceeding max attempts should redirect back to add_email."""
        client.force_login(discord_user)
        self._set_session(client, code="RPQJ-NBKC")
        # Exhaust attempts
        for _ in range(4):
            client.post(reverse("confirm_add_email"), {"code": "XXXX-YYYY"})
        # Session should be cleared, next attempt redirects
        response = client.get(reverse("confirm_add_email"))
        assert response.status_code == HTTPStatus.FOUND

    def test_updates_user_email_if_different(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """If the verified email differs from the user's current email, update it."""
        client.force_login(discord_user)
        new_email = "different@example.com"
        self._set_session(client, email=new_email, code="TKLM-BVWZ")
        client.post(reverse("confirm_add_email"), {"code": "TKLM-BVWZ"})
        discord_user.refresh_from_db()
        assert discord_user.email == new_email

    def test_associates_with_event(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """If an event slug was stored, the user should be linked to the event."""
        event = baker.make(Event, slug="test-event", is_active=True)
        client.force_login(discord_user)
        expires = timezone.now() + timedelta(minutes=5)
        session = client.session
        session[_ADD_EMAIL_SESSION_KEY] = {
            "email": "new@example.com",
            "code": "123456",
            "expires": expires.isoformat(),
            "attempts": 0,
            "event_slug": "test-event",
        }
        session.save()
        client.post(reverse("confirm_add_email"), {"code": "123456"})
        assert discord_user.events.filter(pk=event.pk).exists()

    def test_unauthenticated_redirect(self, client: Any) -> None:
        """Unauthenticated users are redirected."""
        response = client.get(reverse("confirm_add_email"))
        assert response.status_code == HTTPStatus.FOUND

    def test_old_primary_email_demoted(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """When a new email is verified, the old primary EmailAddress is demoted."""
        old_email = EmailAddress.objects.create(
            user=discord_user,
            email="old@example.com",
            verified=True,
            primary=True,
        )
        client.force_login(discord_user)
        self._set_session(client, email="new-ticket@example.com", code="777777")
        client.post(reverse("confirm_add_email"), {"code": "777777"})
        old_email.refresh_from_db()
        assert old_email.primary is False
        new_email = EmailAddress.objects.get(user=discord_user, email="new-ticket@example.com")
        assert new_email.primary is True
        assert new_email.verified is True

    def test_pre_existing_unverified_email_gets_promoted(
        self,
        client: Any,
        discord_user: CustomUser,
    ) -> None:
        """A matching EmailAddress that existed unverified must be updated, not duplicated."""
        existing = EmailAddress.objects.create(
            user=discord_user,
            email="reuse@example.com",
            verified=False,
            primary=False,
        )
        client.force_login(discord_user)
        self._set_session(client, email="reuse@example.com", code="999999")
        client.post(reverse("confirm_add_email"), {"code": "999999"})
        existing.refresh_from_db()
        assert existing.verified is True
        assert existing.primary is True
        # Still exactly one record for this address (not duplicated).
        rows = EmailAddress.objects.filter(user=discord_user, email="reuse@example.com")
        assert rows.count() == 1


# ---------------------------------------------------------------------------
# Error message override
# ---------------------------------------------------------------------------


class TestCustomErrorMessages:
    """Verify the custom error_messages on SocialAccountAdapter."""

    def test_no_password_message_mentions_email(self) -> None:
        """The overridden no_password message should mention adding an email."""
        adapter = SocialAccountAdapter()
        msg = str(adapter.error_messages["no_password"])
        assert "email" in msg.lower()
        assert "password" not in msg.lower() or "without" in msg.lower()

    def test_email_not_authorized_message(self) -> None:
        """The email_not_authorized message should mention ticket purchase."""
        adapter = SocialAccountAdapter()
        msg = str(adapter.error_messages["email_not_authorized"])
        assert "ticket" in msg.lower()


# ---------------------------------------------------------------------------
# can_login_by_email on AccountAdapter
# ---------------------------------------------------------------------------


class TestCanLoginByEmail:
    """Verify AccountAdapter.can_login_by_email checks the validation API."""

    def test_privileged_email_always_allowed(self, settings: Any) -> None:
        """Whitelisted emails bypass the validation API."""
        settings.AUTHORIZED_EMAILS_WHITELIST = ["vip@example.com"]
        adapter = AccountAdapter()
        assert adapter.can_login_by_email("vip@example.com") is True

    @patch("users.adapters.AccountAdapter._call_validation_api")
    def test_api_validated_email_allowed(self, mock_api: Any) -> None:
        """Email recognized by the validation API should return True."""
        baker.make(Event, is_active=True, validation_api_url="https://api.test/validate")
        mock_api.return_value = {"valid": True}
        adapter = AccountAdapter()
        assert adapter.can_login_by_email("ticket@example.com") is True
        mock_api.assert_called_once()

    @patch("users.adapters.AccountAdapter._call_validation_api")
    def test_api_rejected_email_denied(self, mock_api: Any) -> None:
        """Email NOT recognized by the validation API should return False."""
        baker.make(Event, is_active=True, validation_api_url="https://api.test/validate")
        mock_api.return_value = {"valid": False}
        adapter = AccountAdapter()
        assert adapter.can_login_by_email("stranger@example.com") is False

    def test_no_api_configured_denies(self, settings: Any) -> None:
        """Without any validation API, non-privileged emails are denied."""
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = ""
        adapter = AccountAdapter()
        assert adapter.can_login_by_email("nobody@example.com") is False

    @patch("users.adapters.AccountAdapter._call_validation_api")
    def test_fallback_api_used(self, mock_api: Any, settings: Any) -> None:
        """Fallback API URL should be used when no event has validation_api_url."""
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = "https://fallback.test/validate"
        mock_api.return_value = {"valid": True}
        adapter = AccountAdapter()
        assert adapter.can_login_by_email("fallback@example.com") is True
        mock_api.assert_called_once_with("fallback@example.com", "https://fallback.test/validate")

    @patch("users.adapters.AccountAdapter._call_validation_api")
    def test_api_error_denies(self, mock_api: Any, settings: Any) -> None:
        """API errors should deny disconnect (fail closed)."""
        settings.EMAIL_VALIDATION_API_URL_FALLBACK = "https://fallback.test/validate"
        mock_api.side_effect = Exception("connection refused")
        adapter = AccountAdapter()
        assert adapter.can_login_by_email("broken@example.com") is False
