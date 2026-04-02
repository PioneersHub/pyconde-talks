"""Tests for the Discord social account adapter."""

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from model_bakery import baker

from events.models import Event
from users.adapters import SocialAccountAdapter, _DiscordNotInGuildError


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DISCORD_ROLES_MAP = {
    "attendee": "111",
    "organiser": "222",
    "session_chair": "333",
    "program": "444",
    "diversity": "555",
    "steering": "666",
    "volunteer": "777",
}


def _make_sociallogin(
    *,
    provider: str = "discord",
    email: str = "user@example.com",
    verified: bool = True,
    is_existing: bool = False,
    user: Any = None,
    token: str = "fake-token",
    extra_data: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock sociallogin object for testing."""
    sl = MagicMock()
    sl.account.provider = provider
    sl.account.extra_data = extra_data or {"email": email, "verified": verified}
    sl.is_existing = is_existing
    sl.user = user
    sl.token.token = token
    return sl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter() -> SocialAccountAdapter:
    """Return a fresh SocialAccountAdapter instance."""
    return SocialAccountAdapter()


@pytest.fixture()
def rf() -> RequestFactory:
    """Return a Django RequestFactory."""
    return RequestFactory()


@pytest.fixture()
def discord_settings(settings: Any) -> Any:
    """Configure the standard Discord settings for tests."""
    settings.DISCORD_GUILD_ID = "999"
    settings.DISCORD_ROLES = DISCORD_ROLES_MAP
    settings.DISCORD_ALLOWED_ROLES = ["attendee", "organiser", "session_chair", "steering"]
    settings.DISCORD_ADMIN_ROLES = ["organiser", "steering"]
    settings.DISCORD_STAFF_ROLES = ["session_chair"]
    settings.DISCORD_API_TIMEOUT = 1
    # Social account provider config (needed for allauth)
    settings.SOCIALACCOUNT_PROVIDERS = {
        "discord": {
            "SCOPE": ["identify", "email", "guilds.members.read"],
            "APPS": [{"client_id": "test-id", "secret": "test-secret"}],
        },
    }
    return settings


# ---------------------------------------------------------------------------
# _fetch_member_role_ids
# ---------------------------------------------------------------------------


class TestFetchMemberRoleIds:
    """Tests for the _fetch_member_role_ids static method."""

    @patch("users.adapters.httpx.get")
    def test_returns_role_ids(self, mock_get: MagicMock) -> None:
        """Successful API call returns the set of role IDs."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"roles": ["111", "222"]},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        result = SocialAccountAdapter._fetch_member_role_ids("tok", "999")
        assert result == {"111", "222"}

    @patch("users.adapters.httpx.get")
    def test_not_found_raises(self, mock_get: MagicMock) -> None:
        """A 404 response raises _DiscordNotInGuildError."""
        mock_get.return_value = MagicMock(status_code=404)
        with pytest.raises(_DiscordNotInGuildError):
            SocialAccountAdapter._fetch_member_role_ids("tok", "999")

    @patch("users.adapters.httpx.get")
    def test_server_error_raises(self, mock_get: MagicMock) -> None:
        """A 500 response raises httpx.HTTPStatusError."""
        resp = httpx.Response(500, request=httpx.Request("GET", "http://x"))
        mock_get.return_value = resp
        with pytest.raises(httpx.HTTPStatusError):
            SocialAccountAdapter._fetch_member_role_ids("tok", "999")


# ---------------------------------------------------------------------------
# _match_allowed_roles
# ---------------------------------------------------------------------------


class TestMatchAllowedRoles:
    """Tests for the _match_allowed_roles helper."""

    def test_matching_roles(self, adapter: SocialAccountAdapter, discord_settings: Any) -> None:
        """Role IDs that match allowed names are returned."""
        result = adapter._match_allowed_roles({"111", "222"})
        assert result == {"attendee", "organiser"}

    def test_no_match(self, adapter: SocialAccountAdapter, discord_settings: Any) -> None:
        """Unknown role IDs yield an empty set."""
        result = adapter._match_allowed_roles({"999"})
        assert result == set()

    def test_non_allowed_role_excluded(
        self,
        adapter: SocialAccountAdapter,
        discord_settings: Any,
    ) -> None:
        """A role in DISCORD_ROLES but not in DISCORD_ALLOWED_ROLES is excluded."""
        # "program" is in DISCORD_ROLES but not in DISCORD_ALLOWED_ROLES for this test
        discord_settings.DISCORD_ALLOWED_ROLES = ["attendee"]
        result = adapter._match_allowed_roles({"111", "444"})
        assert result == {"attendee"}


# ---------------------------------------------------------------------------
# _apply_initial_permissions
# ---------------------------------------------------------------------------


class TestApplyInitialPermissions:
    """Tests for the _apply_initial_permissions helper (new users only)."""

    def test_admin_role_grants_superuser_and_staff(
        self,
        adapter: SocialAccountAdapter,
        discord_settings: Any,
        user_model: type[Any],
    ) -> None:
        """A new user with an admin role gets is_superuser=True and is_staff=True."""
        user = user_model.objects.create_user(email="admin@test.com")
        adapter._apply_initial_permissions(user, {"organiser"})
        user.refresh_from_db()
        assert user.is_superuser is True
        assert user.is_staff is True

    def test_staff_role_grants_staff_only(
        self,
        adapter: SocialAccountAdapter,
        discord_settings: Any,
        user_model: type[Any],
    ) -> None:
        """A new user with only a staff role gets is_staff=True but not is_superuser."""
        user = user_model.objects.create_user(email="staff@test.com")
        adapter._apply_initial_permissions(user, {"session_chair"})
        user.refresh_from_db()
        assert user.is_superuser is False
        assert user.is_staff is True

    def test_regular_role_no_elevation(
        self,
        adapter: SocialAccountAdapter,
        discord_settings: Any,
        user_model: type[Any],
    ) -> None:
        """A new user with only a regular role keeps default permissions."""
        user = user_model.objects.create_user(email="reg@test.com")
        adapter._apply_initial_permissions(user, {"attendee"})
        user.refresh_from_db()
        assert user.is_superuser is False
        assert user.is_staff is False

    def test_no_save_when_no_elevation(
        self,
        adapter: SocialAccountAdapter,
        discord_settings: Any,
        user_model: type[Any],
    ) -> None:
        """No DB write happens if no admin/staff roles are matched."""
        user = user_model.objects.create_user(email="ok@test.com")
        with patch.object(user, "save") as mock_save:
            adapter._apply_initial_permissions(user, {"attendee"})
            mock_save.assert_not_called()

    def test_empty_admin_and_staff_roles(
        self,
        adapter: SocialAccountAdapter,
        discord_settings: Any,
        user_model: type[Any],
    ) -> None:
        """Empty DISCORD_ADMIN_ROLES and DISCORD_STAFF_ROLES never elevate."""
        discord_settings.DISCORD_ADMIN_ROLES = []
        discord_settings.DISCORD_STAFF_ROLES = []
        user = user_model.objects.create_user(email="noperms@test.com")
        adapter._apply_initial_permissions(user, {"organiser", "session_chair"})
        user.refresh_from_db()
        assert user.is_superuser is False
        assert user.is_staff is False


# ---------------------------------------------------------------------------
# pre_social_login
# ---------------------------------------------------------------------------


class TestPreSocialLogin:
    """Tests for the pre_social_login hook."""

    def test_non_discord_provider_passes_through(
        self,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """Providers other than Discord are ignored."""
        sl = _make_sociallogin(provider="google")
        request = rf.get("/")
        adapter.pre_social_login(request, sl)  # Should not raise

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_not_in_guild_rejected(
        self,
        mock_fetch: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """A user who is not in the guild is rejected."""
        mock_fetch.side_effect = _DiscordNotInGuildError
        sl = _make_sociallogin()
        request = rf.get("/")
        with pytest.raises(ImmediateHttpResponse) as exc_info:
            adapter.pre_social_login(request, sl)
        assert "not_in_server" in exc_info.value.response.url

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_http_error_rejected(
        self,
        mock_fetch: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """An HTTP error during the role check redirects with discord_error."""
        mock_fetch.side_effect = httpx.HTTPError("fail")
        sl = _make_sociallogin()
        request = rf.get("/")
        with pytest.raises(ImmediateHttpResponse) as exc_info:
            adapter.pre_social_login(request, sl)
        assert "discord_error" in exc_info.value.response.url

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_no_allowed_roles_rejected(
        self,
        mock_fetch: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """A user with no allowed roles is rejected."""
        mock_fetch.return_value = {"999"}  # No matching role ID
        sl = _make_sociallogin()
        request = rf.get("/")
        with pytest.raises(ImmediateHttpResponse) as exc_info:
            adapter.pre_social_login(request, sl)
        assert "missing_role" in exc_info.value.response.url

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_existing_social_account_passes_through(
        self,
        mock_fetch: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
        user_model: type[Any],
    ) -> None:
        """An existing social account just passes through without touching permissions."""
        user = user_model.objects.create_user(email="existing@test.com")
        mock_fetch.return_value = {"111"}  # attendee
        sl = _make_sociallogin(is_existing=True, user=user)
        request = rf.get("/")
        adapter.pre_social_login(request, sl)
        # Permissions should NOT be modified for existing users
        user.refresh_from_db()
        assert user.is_superuser is False
        assert user.is_staff is False

    @patch.object(SocialAccountAdapter, "_connect_to_existing_account")
    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_new_login_connects_to_existing_email_account(
        self,
        mock_fetch: MagicMock,
        mock_connect: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """A new Discord login tries to connect to an existing email-based account."""
        mock_fetch.return_value = {"111"}
        sl = _make_sociallogin(email="match@test.com")
        request = rf.get("/")
        adapter.pre_social_login(request, sl)
        mock_connect.assert_called_once_with(request, sl)

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_new_login_no_existing_email_proceeds(
        self,
        mock_fetch: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """A brand-new Discord user with no existing email passes through to save_user."""
        mock_fetch.return_value = {"111"}
        sl = _make_sociallogin(email="newuser@test.com")
        request = rf.get("/")
        adapter.pre_social_login(request, sl)
        # matched_roles stored in extra_data for save_user to use
        assert "matched_roles" in sl.account.extra_data

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_unverified_email_still_authenticates(
        self,
        mock_fetch: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """A Discord account with unverified email can still log in (role is enough)."""
        mock_fetch.return_value = {"111"}  # attendee
        sl = _make_sociallogin(verified=False)
        request = rf.get("/")
        adapter.pre_social_login(request, sl)  # Should not raise
        assert "matched_roles" in sl.account.extra_data

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_matched_roles_stored_in_extra_data(
        self,
        mock_fetch: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """The matched role names are stored in sociallogin.account.extra_data."""
        mock_fetch.return_value = {"111", "222"}  # attendee + organiser
        sl = _make_sociallogin(is_existing=True, user=MagicMock())
        request = rf.get("/")
        adapter.pre_social_login(request, sl)
        assert sorted(sl.account.extra_data["matched_roles"]) == ["attendee", "organiser"]


# ---------------------------------------------------------------------------
# _connect_to_existing_account
# ---------------------------------------------------------------------------


class TestConnectToExistingAccount:
    """Tests for connecting a social login to an existing email account."""

    def test_connect_when_verified_email_matches(
        self,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        user_model: type[Any],
    ) -> None:
        """Social login connects to an existing user when Discord email is verified."""
        user = user_model.objects.create_user(email="link@test.com")
        sl = _make_sociallogin(email="link@test.com", verified=True)
        request = rf.get("/")
        adapter._connect_to_existing_account(request, sl)
        sl.connect.assert_called_once_with(request, user)

    def test_no_connect_when_email_unverified(
        self,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        user_model: type[Any],
    ) -> None:
        """No connection attempt when Discord email is not verified."""
        user_model.objects.create_user(email="link@test.com")
        sl = _make_sociallogin(email="link@test.com", verified=False)
        request = rf.get("/")
        adapter._connect_to_existing_account(request, sl)
        sl.connect.assert_not_called()

    def test_no_connect_when_email_missing(
        self,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
    ) -> None:
        """No connection attempt when Discord extra_data has no email."""
        sl = _make_sociallogin(extra_data={"verified": True})
        request = rf.get("/")
        adapter._connect_to_existing_account(request, sl)
        sl.connect.assert_not_called()

    def test_no_connect_when_email_not_found(
        self,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
    ) -> None:
        """No connection when no EmailAddress record exists for the email."""
        sl = _make_sociallogin(email="nobody@test.com")
        request = rf.get("/")
        adapter._connect_to_existing_account(request, sl)
        sl.connect.assert_not_called()

    def test_case_insensitive_email_match(
        self,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        user_model: type[Any],
    ) -> None:
        """Email matching is case-insensitive."""
        user_model.objects.create_user(email="case@test.com")
        sl = _make_sociallogin(email="CASE@TEST.COM", verified=True)
        request = rf.get("/")
        adapter._connect_to_existing_account(request, sl)
        sl.connect.assert_called_once()


# ---------------------------------------------------------------------------
# _try_merge_accounts
# ---------------------------------------------------------------------------


def _add_session(request: Any) -> None:
    """Attach session and messages middleware to a RequestFactory-produced request."""
    noop: Any = lambda _: None  # noqa: E731
    SessionMiddleware(noop).process_request(request)
    request.session.save()
    MessageMiddleware(noop).process_request(request)


class TestTryMergeAccounts:
    """Tests for the _try_merge_accounts helper."""

    @patch("users.adapters.get_account_adapter")
    def test_merge_deletes_orphan_and_reassigns(
        self,
        mock_get_adapter: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        user_model: type[Any],
    ) -> None:
        """Orphan user is deleted and the SocialAccount is assigned to the current user."""
        current_user = user_model.objects.create_user(email="me@test.com")
        orphan = user_model.objects.create_user(email="orphan@test.com")
        sa = SocialAccount.objects.create(
            user=orphan,
            provider="discord",
            uid="merge-uid",
            extra_data={},
        )

        mock_get_adapter.return_value.can_login_by_email.return_value = True

        sl = MagicMock()
        sl.account = sa
        sl.user = orphan
        sl.is_existing = True

        request = rf.get("/")
        request.user = current_user
        _add_session(request)

        with pytest.raises(ImmediateHttpResponse):
            adapter._try_merge_accounts(request, sl)

        sa.refresh_from_db()
        assert sa.user == current_user
        assert not user_model.objects.filter(pk=orphan.pk).exists()

    @patch("users.adapters.get_account_adapter")
    def test_merge_transfers_events(
        self,
        mock_get_adapter: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        user_model: type[Any],
    ) -> None:
        """Event associations are transferred from the orphan to the current user."""
        current_user = user_model.objects.create_user(email="me@test.com")
        orphan = user_model.objects.create_user(email="orphan@test.com")
        event = Event.objects.create(slug="evt", name="E", year=2026)
        orphan.events.add(event)
        sa = SocialAccount.objects.create(
            user=orphan,
            provider="discord",
            uid="merge-uid-2",
            extra_data={},
        )

        mock_get_adapter.return_value.can_login_by_email.return_value = True

        sl = MagicMock()
        sl.account = sa
        sl.user = orphan
        sl.is_existing = True

        request = rf.get("/")
        request.user = current_user
        _add_session(request)

        with pytest.raises(ImmediateHttpResponse):
            adapter._try_merge_accounts(request, sl)

        assert current_user.events.filter(pk=event.pk).exists()

    @patch("users.adapters.get_account_adapter")
    def test_no_merge_when_no_verified_email(
        self,
        mock_get_adapter: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        user_model: type[Any],
    ) -> None:
        """Merge is skipped if the current user has no verified email."""
        # Use baker.make so no EmailAddress is auto-created.
        current_user = baker.make(user_model, email="me@test.com")
        orphan = user_model.objects.create_user(email="orphan@test.com")
        SocialAccount.objects.create(
            user=orphan,
            provider="discord",
            uid="no-merge",
            extra_data={},
        )

        sl = MagicMock()
        sl.user = orphan
        sl.is_existing = True

        request = rf.get("/")
        request.user = current_user

        adapter._try_merge_accounts(request, sl)

        # Orphan should still exist
        assert user_model.objects.filter(pk=orphan.pk).exists()

    @patch("users.adapters.get_account_adapter")
    def test_no_merge_when_email_not_validated(
        self,
        mock_get_adapter: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        user_model: type[Any],
    ) -> None:
        """Merge is skipped if the current user's email is not API-validated."""
        current_user = user_model.objects.create_user(email="me@test.com")
        orphan = user_model.objects.create_user(email="orphan@test.com")
        SocialAccount.objects.create(
            user=orphan,
            provider="discord",
            uid="no-merge-2",
            extra_data={},
        )

        mock_get_adapter.return_value.can_login_by_email.return_value = False

        sl = MagicMock()
        sl.user = orphan
        sl.is_existing = True

        request = rf.get("/")
        request.user = current_user

        adapter._try_merge_accounts(request, sl)

        assert user_model.objects.filter(pk=orphan.pk).exists()


# ---------------------------------------------------------------------------
# save_user
# ---------------------------------------------------------------------------


class TestSaveUser:
    """Tests for the save_user hook."""

    @patch.object(SocialAccountAdapter, "_apply_initial_permissions")
    def test_permissions_applied_on_new_signup(
        self,
        mock_apply: MagicMock,
        adapter: SocialAccountAdapter,
        rf: RequestFactory,
        discord_settings: Any,
    ) -> None:
        """Initial permissions are set when a brand-new user is created."""
        sl = _make_sociallogin(
            extra_data={
                "email": "new@test.com",
                "verified": True,
                "matched_roles": ["organiser"],
            },
        )
        request = rf.get("/")
        with patch(
            "users.adapters.DefaultSocialAccountAdapter.save_user",
        ) as mock_super:
            mock_user = MagicMock()
            mock_super.return_value = mock_user
            adapter.save_user(request, sl)
            mock_apply.assert_called_once_with(mock_user, {"organiser"})


# ---------------------------------------------------------------------------
# Role property defaults
# ---------------------------------------------------------------------------


class TestRoleProperties:
    """Tests for the role name properties."""

    def test_empty_by_default(self, settings: Any) -> None:
        """All role sets are empty when settings are not configured."""
        del settings.DISCORD_ALLOWED_ROLES
        del settings.DISCORD_ADMIN_ROLES
        del settings.DISCORD_STAFF_ROLES
        adapter = SocialAccountAdapter()
        assert adapter._allowed_roles == frozenset()
        assert adapter._admin_roles == frozenset()
        assert adapter._staff_roles == frozenset()

    def test_custom_roles_from_settings(self, discord_settings: Any) -> None:
        """Custom role lists from settings are used."""
        discord_settings.DISCORD_ALLOWED_ROLES = ["custom_role"]
        adapter = SocialAccountAdapter()
        assert adapter._allowed_roles == frozenset({"custom_role"})

    def test_empty_allowed_roles_rejects_all(
        self,
        adapter: SocialAccountAdapter,
        discord_settings: Any,
    ) -> None:
        """Empty DISCORD_ALLOWED_ROLES means no role IDs can match."""
        discord_settings.DISCORD_ALLOWED_ROLES = []
        result = adapter._match_allowed_roles({"111", "222"})
        assert result == set()


# ---------------------------------------------------------------------------
# _add_default_event
# ---------------------------------------------------------------------------


class TestAddDefaultEvent:
    """Tests for the _add_default_event helper."""

    def test_adds_default_event(self, settings: Any, user_model: type[Any]) -> None:
        """User is associated with the DEFAULT_EVENT."""
        event = Event.objects.create(slug="pycon-2026", name="PyCon 2026", year=2026)
        settings.DEFAULT_EVENT = "pycon-2026"
        user = user_model.objects.create_user(email="test@example.com")
        SocialAccountAdapter._add_default_event(user)
        assert event in user.events.all()

    def test_no_op_when_already_associated(self, settings: Any, user_model: type[Any]) -> None:
        """No duplicate is created if the user already has the event."""
        event = Event.objects.create(slug="pycon-2026", name="PyCon 2026", year=2026)
        settings.DEFAULT_EVENT = "pycon-2026"
        user = user_model.objects.create_user(email="test@example.com")
        user.events.add(event)
        SocialAccountAdapter._add_default_event(user)
        assert user.events.count() == 1

    def test_no_op_when_setting_empty(self, settings: Any, user_model: type[Any]) -> None:
        """No error when DEFAULT_EVENT is empty."""
        settings.DEFAULT_EVENT = ""
        user = user_model.objects.create_user(email="test@example.com")
        SocialAccountAdapter._add_default_event(user)
        assert user.events.count() == 0

    def test_no_op_when_event_not_found(self, settings: Any, user_model: type[Any]) -> None:
        """No error when the DEFAULT_EVENT slug doesn't match any Event."""
        settings.DEFAULT_EVENT = "nonexistent"
        user = user_model.objects.create_user(email="test@example.com")
        SocialAccountAdapter._add_default_event(user)
        assert user.events.count() == 0

    @patch.object(SocialAccountAdapter, "_fetch_member_role_ids")
    def test_existing_login_gets_default_event(
        self,
        mock_fetch: MagicMock,
        discord_settings: Any,
        user_model: type[Any],
    ) -> None:
        """An existing social account login adds the DEFAULT_EVENT."""
        event = Event.objects.create(slug="pycon-2026", name="PyCon 2026", year=2026)
        discord_settings.DEFAULT_EVENT = "pycon-2026"
        mock_fetch.return_value = {"111"}  # attendee
        user = user_model.objects.create_user(email="existing@test.com")
        sl = _make_sociallogin(is_existing=True, user=user)
        request = RequestFactory().get("/")
        SocialAccountAdapter().pre_social_login(request, sl)
        assert event in user.events.all()

    @patch.object(SocialAccountAdapter, "_apply_initial_permissions")
    def test_save_user_gets_default_event(
        self,
        mock_apply: MagicMock,
        discord_settings: Any,
    ) -> None:
        """A brand-new user created via save_user gets the DEFAULT_EVENT."""
        event = Event.objects.create(slug="pycon-2026", name="PyCon 2026", year=2026)
        discord_settings.DEFAULT_EVENT = "pycon-2026"
        sl = _make_sociallogin(
            extra_data={"email": "new@test.com", "verified": True, "matched_roles": ["attendee"]},
        )
        request = RequestFactory().get("/")
        with patch("users.adapters.DefaultSocialAccountAdapter.save_user") as mock_super:
            mock_user = MagicMock()
            mock_user.events = MagicMock()
            mock_user.events.filter.return_value.exists.return_value = False
            mock_super.return_value = mock_user
            SocialAccountAdapter().save_user(request, sl)
            mock_user.events.add.assert_called_once_with(event)
