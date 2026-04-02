"""Custom adapters for django-allauth (e-mail and Discord social login)."""

from http import HTTPStatus
from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, OperationalError
from django.shortcuts import redirect
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from utils.email_utils import hash_email


if TYPE_CHECKING:
    from django.http import HttpRequest

    from events.models import Event
    from users.models import CustomUser


logger = structlog.get_logger(__name__)

_DISCORD_API = "https://discord.com/api/v10"


class AccountAdapter(DefaultAccountAdapter):  # type: ignore[misc]
    """
    Custom adapter for django-allauth that validates emails using an external API.

    This adapter implements a multi-layered, event-aware authorization strategy:
    1. Superusers and whitelisted emails are always authorized.
    2. If the user already exists and is associated with the selected event, let them in.
    3. If the user exists but is NOT associated with the selected event, call the event's validation
       API. If valid, associate the user with the event.
    4. If the user does not exist, call the event's validation API. If valid, the user will be
       created and associated with the event.

    The selected event is passed via the login form's ``event`` field and stored on the adapter as
    ``self._selected_event``.
    """

    _selected_event: Event | None = None

    def set_selected_event(self, event: Event | None) -> None:
        """Store the event selected on the login page for use in authorization checks."""
        self._selected_event = event

    def is_email_authorized(self, email: str) -> bool:
        """
        Validate if email is authorized for login to the selected event.

        Authorization order:
        1. Whitelist / superuser → always allowed.
        2. Existing active user already linked to the event → allowed.
        3. Existing user NOT yet linked → call event validation API → link on success.
        4. New user → call event validation API (user will be created afterwards).

        Args:
            email: The email address to validate

        Returns:
            bool: True if the email is authorized, False otherwise (including on API errors)

        """
        email = email.lower().strip()
        email_hash = hash_email(email)

        # Superusers and whitelisted emails bypass everything
        if self._is_privileged(email, email_hash):
            return True

        event = self._selected_event

        # Look up the user (may not exist yet)
        UserModel = cast("type[CustomUser]", get_user_model())  # noqa: N806  # NOSONAR(S117)
        user: CustomUser | None = None
        try:
            user = UserModel.objects.get(email=email)
        except UserModel.DoesNotExist:
            pass
        except DatabaseError, OperationalError:  # pragma: no cover
            logger.exception("Database error looking up user", email=email_hash)
            return False

        # If user exists, is active, and already linked to this event -> allow
        if user and user.is_active and event and user.events.filter(pk=event.pk).exists():
            logger.info(
                "User already associated with event",
                email=email_hash,
                event_slug=event.slug,
            )
            return True

        # Otherwise, call the event's validation API
        if not self._validate_email_for_event(email, email_hash, event):
            return False

        # Validation passed - associate the existing user with the event
        # (new users are handled later when the user is created in the view).
        if user and event:
            user.events.add(event)
            logger.info(
                "Associated existing user with event",
                email=email_hash,
                event_slug=event.slug,
            )

        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_privileged(email: str, email_hash: str) -> bool:
        """Check if email is whitelisted or belongs to a superuser."""
        if email in getattr(settings, "AUTHORIZED_EMAILS_WHITELIST", []):
            logger.info("Email found in whitelist", email=email_hash)
            return True

        UserModel = cast("type[CustomUser]", get_user_model())  # noqa: N806  # NOSONAR(S117)
        try:
            user = UserModel.objects.get(email=email)
            if user.is_superuser:
                logger.info("Admin authorized", email=email_hash)
                return True
        except UserModel.DoesNotExist:
            pass
        except DatabaseError, OperationalError:  # pragma: no cover
            logger.exception("Database error checking privileged status", email=email_hash)
        return False

    def _validate_email_for_event(
        self,
        email: str,
        email_hash: str,
        event: Event | None,
    ) -> bool:
        """Validate email using the event's validation API URL (or the global fallback)."""
        api_url = ""
        if event and event.validation_api_url:
            api_url = event.validation_api_url
        else:
            api_url = getattr(settings, "EMAIL_VALIDATION_API_URL_FALLBACK", "")

        if not api_url:
            logger.info(
                "No validation API configured; denying non-privileged auth",
                email=email_hash,
            )
            return False

        is_valid = False
        try:
            data = self._call_validation_api(email, api_url)
            is_valid = data.get("valid", False)

            if is_valid:
                logger.info("Successfully validated email", email=email_hash)
            else:
                logger.warning("Email validation failed", email=email_hash)

        except httpx.TimeoutException:  # pragma: no cover
            logger.warning("Timeout validating email", email=email_hash)
        except httpx.ConnectError:  # pragma: no cover
            logger.warning("Connection error validating email", email=email_hash)
        except JSONDecodeError:  # pragma: no cover
            logger.warning("Invalid JSON response validating email", email=email_hash)
        except httpx.HTTPError as exc:  # pragma: no cover
            logger.warning("Request error validating email", email=email_hash, error=str(exc))
        except Exception:  # pragma: no cover
            logger.exception("Unexpected error validating email", email=email_hash)

        return is_valid

    @staticmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    def _call_validation_api(email: str, api_url: str) -> dict[str, Any]:
        """Call the validation API with retry."""
        response = httpx.post(
            api_url,
            json={"email": email},
            timeout=getattr(settings, "EMAIL_VALIDATION_API_TIMEOUT", 5),
        )
        response.raise_for_status()
        return dict(response.json())

    def send_mail(self, template_prefix: str, email: str, context: dict[str, Any]) -> None:
        """Add custom variables to the email context before sending."""
        timeout_seconds = getattr(settings, "ACCOUNT_LOGIN_BY_CODE_TIMEOUT", 180)
        timeout_minutes = round(timeout_seconds / 60, 1)
        context["login_code_timeout_minutes"] = (
            int(timeout_minutes) if timeout_minutes.is_integer() else round(timeout_minutes, 1)
        )
        # Inject branding variables from the selected event
        event = self._selected_event
        event_name = event.name if event else ""
        event_year = str(event.year) if event else ""
        prefix = f"{event_name} " if event_name else ""
        context.update(
            {
                "brand_event_name": event_name,
                "brand_event_year": event_year,
                "brand_title": f"{prefix}Talks",
            },
        )
        super().send_mail(template_prefix, email, context)


# ---------------------------------------------------------------------------
# Discord / social adapter
# ---------------------------------------------------------------------------


class _DiscordNotInGuildError(Exception):
    """Raised when the Discord user is not a member of the required guild."""


class SocialAccountAdapter(DefaultSocialAccountAdapter):  # type: ignore[misc]
    """
    Enforce Discord role-based access control and prevent duplicate accounts.

    Links Discord logins to existing email-based accounts.
    Login is granted only to users who hold at least one role listed in
    ``settings.DISCORD_ALLOWED_ROLES``. If the list is empty, all Discord logins are rejected.

    Role-to-Django permission mapping (applied only on new user creation via ``save_user``):
    - ``DISCORD_ADMIN_ROLES``: grants ``is_superuser = True`` and ``is_staff = True``
    - ``DISCORD_STAFF_ROLES``: grants ``is_staff = True``
    - all others / empty lists: no elevated permissions

    Existing users keep their current permissions on subsequent logins. Admin/staff flags can be
    managed through the Django admin interface independently of Discord roles.

    Required settings::

        DISCORD_GUILD_ID       str        Your server's numeric ID
        DISCORD_ROLES          dict       Full {role_name: role_id} map
        DISCORD_ALLOWED_ROLES  list[str]  Role names permitted to log in (empty = no access)

    Optional settings::

        DISCORD_ADMIN_ROLES  list[str]  Role names that grant is_superuser + is_staff on signup
        DISCORD_STAFF_ROLES  list[str]  Role names that grant is_staff only on signup
        DISCORD_API_TIMEOUT  int        Seconds before Discord API calls time out (default 5)
    """

    # ------------------------------------------------------------------
    # allauth hooks
    # ------------------------------------------------------------------

    def pre_social_login(self, request: HttpRequest, sociallogin: Any) -> None:
        """
        Run after Discord authenticates the user but before the session is created.

        Order of operations:
        1. Only applies to Discord logins (other providers pass through).
        2. Check the user holds at least one allowed Discord role; reject if not.
        3. For already-connected social accounts: just return (permissions are not touched).
        4. For brand-new social logins: try to connect to an existing email-based account
           with the same address (requires a verified Discord email).
           If no existing account is found, allauth proceeds to ``save_user`` (new signup).
        """
        if sociallogin.account.provider != "discord":
            return

        # Step 1: role check
        try:
            member_role_ids = self._fetch_member_role_ids(
                token=sociallogin.token.token,
                guild_id=settings.DISCORD_GUILD_ID,
            )
        except _DiscordNotInGuildError:
            logger.warning("Discord login rejected: user not in guild")
            raise ImmediateHttpResponse(
                redirect("/accounts/login/?error=not_in_server"),
            ) from None
        except httpx.HTTPError as exc:
            logger.warning("Discord API error during role check", error=str(exc))
            raise ImmediateHttpResponse(
                redirect("/accounts/login/?error=discord_error"),
            ) from exc

        matched_names = self._match_allowed_roles(member_role_ids)
        if not matched_names:
            logger.warning("Discord login rejected: no allowed roles")
            raise ImmediateHttpResponse(redirect("/accounts/login/?error=missing_role"))

        logger.info("Discord login authorised", matched_roles=sorted(matched_names))
        sociallogin.account.extra_data["matched_roles"] = sorted(matched_names)

        # Step 2: existing social account - nothing more to do
        if sociallogin.is_existing:
            return

        # Step 3: new social login - connect to existing email account if possible
        self._connect_to_existing_account(request, sociallogin)

    def save_user(self, request: HttpRequest, sociallogin: Any, form: Any = None) -> Any:
        """Persist a brand-new user and set initial permissions based on Discord roles."""
        user = super().save_user(request, sociallogin, form)
        matched_names = set(sociallogin.account.extra_data.get("matched_roles", []))
        self._apply_initial_permissions(user, matched_names)
        return user

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect_to_existing_account(
        self,
        request: HttpRequest,
        sociallogin: Any,
    ) -> None:
        """
        Connect the social account to an existing email-based account.

        If the Discord account has a verified email and an ``EmailAddress`` record already exists
        for it, connect the social account to that user. This prevents a second User from being
        created when someone has already registered via the passwordless e-mail flow.

        A verified Discord email is required for this step because we need to trust that the Discord
        user actually owns that address before linking accounts.

        Note: ``sociallogin.connect()`` completes the login and raises ``ImmediateHttpResponse``, so
        no code after that call will execute when a match is found.
        """
        if not sociallogin.account.extra_data.get("verified", False):
            return  # Cannot trust unverified email for account linking

        email: str = sociallogin.account.extra_data.get("email", "").lower().strip()
        if not email:
            return

        try:
            existing_email = EmailAddress.objects.get(email__iexact=email)
        except EmailAddress.DoesNotExist:
            return  # No existing account - allauth will call save_user for a new signup

        existing_user = existing_email.user

        logger.info(
            "Connected Discord social account to existing email account",
            user_pk=existing_user.pk,
        )
        sociallogin.connect(request, existing_user)

    def _match_allowed_roles(self, member_role_ids: set[str]) -> set[str]:
        """Return the subset of allowed role names the member actually holds."""
        role_map: dict[str, str] = getattr(settings, "DISCORD_ROLES", {})
        allowed = self._allowed_roles
        return {
            name
            for name, role_id in role_map.items()
            if role_id in member_role_ids and name in allowed
        }

    def _apply_initial_permissions(self, user: Any, matched_names: set[str]) -> None:
        """Set ``is_superuser`` and ``is_staff`` for a brand-new user based on Discord roles."""
        new_superuser = bool(matched_names & self._admin_roles)
        new_staff = new_superuser or bool(matched_names & self._staff_roles)

        if new_superuser or new_staff:
            user.is_superuser = new_superuser
            user.is_staff = new_staff
            user.save(update_fields=["is_superuser", "is_staff"])
            logger.info(
                "Set initial Discord role permissions",
                user_pk=user.pk,
                is_superuser=new_superuser,
                is_staff=new_staff,
            )

    @staticmethod
    def _fetch_member_role_ids(token: str, guild_id: str) -> set[str]:
        """
        Return the set of role IDs the authenticated user holds in the guild.

        Calls ``GET /users/@me/guilds/{guild_id}/member`` with the OAuth Bearer token (requires the
        ``guilds.members.read`` scope).

        Raises:
            _DiscordNotInGuildError: if the user is not a member of the guild.
            httpx.HTTPError: on any other non-2xx response or network failure.

        """
        response = httpx.get(
            f"{_DISCORD_API}/users/@me/guilds/{guild_id}/member",
            headers={"Authorization": f"Bearer {token}"},
            timeout=getattr(settings, "DISCORD_API_TIMEOUT", 5),
        )
        if response.status_code == HTTPStatus.NOT_FOUND:
            raise _DiscordNotInGuildError
        response.raise_for_status()
        return set(response.json().get("roles", []))

    # ------------------------------------------------------------------
    # Role name sets from settings (empty when not configured)
    # ------------------------------------------------------------------

    @property
    def _allowed_roles(self) -> frozenset[str]:
        return frozenset(getattr(settings, "DISCORD_ALLOWED_ROLES", []))

    @property
    def _admin_roles(self) -> frozenset[str]:
        return frozenset(getattr(settings, "DISCORD_ADMIN_ROLES", []))

    @property
    def _staff_roles(self) -> frozenset[str]:
        return frozenset(getattr(settings, "DISCORD_STAFF_ROLES", []))
