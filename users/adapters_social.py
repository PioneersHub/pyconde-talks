"""
Discord social-account adapter for django-allauth.

Split out from ``users.adapters`` so the Discord-specific role-check / merge logic is separate from
the email-validation adapter.
"""

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from allauth.account.adapter import get_adapter as get_account_adapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from events.models import Event


# Provider key used by django-allauth's Discord integration. Centralized here so the
# string literal isn't scattered across views and adapter code.
DISCORD_PROVIDER = "discord"


if TYPE_CHECKING:
    from django.http import HttpRequest

    from users.models import CustomUser

    from .adapters import AccountAdapter


logger = structlog.get_logger(__name__)

_DISCORD_API = "https://discord.com/api/v10"


class _DiscordNotInGuildError(Exception):
    """Raised when the Discord user is not a member of the required guild."""


class SocialAccountAdapter(DefaultSocialAccountAdapter):  # type: ignore[misc]
    """
    Enforce Discord role-based access control and prevent duplicate accounts.

    Links Discord logins to existing email-based accounts.
    Login is granted only to users who hold at least one role listed in
    ``settings.DISCORD_ALLOWED_ROLES``. If the list is empty, all Discord logins are rejected.

    Role-to-Django permission mapping (applied when a Discord account is connected - on brand-new
    signups via ``save_user``, when linking to an existing email-based account, and when merging an
    orphan Discord account into an authenticated user):
    - ``DISCORD_ADMIN_ROLES``: grants ``is_superuser = True`` and ``is_staff = True``
    - ``DISCORD_STAFF_ROLES``: grants ``is_staff = True``
    - all others / empty lists: no elevated permissions

    Role application is additive: it only ever promotes. Existing ``is_superuser``/``is_staff``
    flags are never cleared by Discord roles, so admin/staff granted manually (or by a prior login)
    are preserved. Subsequent logins of an already-linked Discord account do not re-apply role
    permissions.

    Required settings::

        DISCORD_GUILD_ID       str        Your server's numeric ID
        DISCORD_ROLES          dict       Full {role_name: role_id} map
        DISCORD_ALLOWED_ROLES  list[str]  Role names permitted to log in (empty = no access)

    Optional settings::

        DISCORD_ADMIN_ROLES  list[str]  Role names that grant is_superuser + is_staff on signup
        DISCORD_STAFF_ROLES  list[str]  Role names that grant is_staff only on signup
        DISCORD_API_TIMEOUT  int        Seconds before Discord API calls time out (default 5)
    """

    error_messages = DefaultSocialAccountAdapter.error_messages | {
        "no_password": _(
            "You cannot remove Discord without a verified email address. "
            "Please add and verify an email first.",
        ),
        "email_not_authorized": _(
            "Your email is not authorized for independent login. "
            "You can only remove Discord if your email is associated "
            "with a ticket purchase.",
        ),
    }

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
        if sociallogin.account.provider != DISCORD_PROVIDER:
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

        # Step 2: existing social account
        if sociallogin.is_existing:
            # Auto-merge: if the authenticated user differs from the social
            # account owner and has an API-validated email, absorb the orphan.
            current_user = getattr(request, "user", None)
            if (
                current_user is not None
                and current_user.is_authenticated
                and sociallogin.user != current_user
            ):
                self._try_merge_accounts(request, sociallogin)
            self._add_default_event(sociallogin.user)
            return

        # Step 3: new social login - connect to existing email account if possible
        self._connect_to_existing_account(request, sociallogin)

    def save_user(self, request: HttpRequest, sociallogin: Any, form: Any = None) -> Any:
        """Persist a brand-new user and set initial permissions based on Discord roles."""
        user = super().save_user(request, sociallogin, form)
        self._grant_role_permissions(user, self._matched_roles(sociallogin))
        self._add_default_event(user)
        return user

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_merge_accounts(
        self,
        request: HttpRequest,
        sociallogin: Any,
    ) -> None:
        """
        Merge an orphan social-only account into the authenticated user.

        When a user with an API-validated email tries to link Discord but the
        Discord account already belongs to a different (orphan) user, this method:
        1. Verifies the current user has a validated email.
        2. Transfers event associations from the orphan.
        3. Reassigns the SocialAccount to the current user.
        4. Deletes the orphan user.
        """
        current_user = cast("CustomUser", request.user)
        orphan_user = sociallogin.user

        # The current user must have an API-validated email.
        verified_email = (
            EmailAddress.objects.filter(user=current_user, verified=True)
            .order_by("-primary")
            .values_list("email", flat=True)
            .first()
        )
        if not verified_email:
            return

        adapter = cast("AccountAdapter", get_account_adapter(request))
        if not adapter.can_login_by_email(verified_email):
            return

        # Transfer event associations.
        for event in orphan_user.events.all():
            current_user.events.add(event)

        # Move the SocialAccount to the current user.
        sociallogin.account.user = current_user
        sociallogin.account.save(update_fields=["user_id"])
        sociallogin.user = current_user

        # Grant Discord-role permissions to the current user (additive only).
        self._grant_role_permissions(current_user, self._matched_roles(sociallogin))

        # Delete the orphan user (cascades EmailAddress, Ticket, etc.).
        orphan_pk = orphan_user.pk
        orphan_user.delete()

        logger.info(
            "Merged orphan Discord account into authenticated user",
            orphan_pk=orphan_pk,
            target_pk=current_user.pk,
        )

        messages.success(
            request,
            _("Your Discord account has been linked and the duplicate account removed."),
            extra_tags="connections",
        )
        raise ImmediateHttpResponse(redirect("socialaccount_connections"))

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
        self._add_default_event(existing_user)
        # Grant Discord-role permissions (additive) before ``connect`` raises and exits.
        self._grant_role_permissions(existing_user, self._matched_roles(sociallogin))
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

    @staticmethod
    def _add_default_event(user: Any) -> None:
        """Associate the user with the DEFAULT_EVENT if configured."""
        slug = getattr(settings, "DEFAULT_EVENT", "")
        if not slug:
            return
        try:
            event = Event.objects.get(slug=slug)
        except Event.DoesNotExist:
            logger.warning("DEFAULT_EVENT not found", slug=slug)
            return
        if not user.events.filter(pk=event.pk).exists():
            user.events.add(event)
            logger.info("Associated user with default event", user_pk=user.pk, event_slug=slug)

    @staticmethod
    def _matched_roles(sociallogin: Any) -> set[str]:
        """Return the set of allowed Discord role names stored by ``pre_social_login``."""
        return set(sociallogin.account.extra_data.get("matched_roles", []))

    def _grant_role_permissions(self, user: Any, matched_names: set[str]) -> None:
        """
        Additively grant ``is_superuser`` / ``is_staff`` based on Discord roles.

        Never demotes: permissions already set on the user (whether granted manually or by a
        previous Discord login) are preserved. Only flips flags from ``False`` to ``True``.
        """
        grant_superuser = bool(matched_names & self._admin_roles)
        grant_staff = grant_superuser or bool(matched_names & self._staff_roles)

        updated_fields: list[str] = []
        if grant_superuser and not user.is_superuser:
            user.is_superuser = True
            updated_fields.append("is_superuser")
        if grant_staff and not user.is_staff:
            user.is_staff = True
            updated_fields.append("is_staff")

        if updated_fields:
            user.save(update_fields=updated_fields)
            logger.info(
                "Granted Discord role permissions",
                user_pk=user.pk,
                granted=updated_fields,
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
