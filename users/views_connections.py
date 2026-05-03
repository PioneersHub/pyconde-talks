"""
Connection management views for Discord/social accounts.

Split out from ``users.views`` so the wrapper around allauth's ConnectionsView and the
"Discord-only user adds a ticket email" two-step flow live together. The core login, profile,
and account-deletion views stay in ``users.views``.
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import structlog
from allauth.account.adapter import get_adapter
from allauth.account.models import EmailAddress
from allauth.core.internal.cryptokit import generate_user_code  # cspell:words cryptokit
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.views import ConnectionsView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from events.models import Event
from users.adapters_social import DISCORD_PROVIDER
from utils.email_utils import hash_email, obfuscate_email


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from .adapters import AccountAdapter
    from .models import CustomUser


logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _ConnectionStatus:
    """Snapshot of a user's Discord / email-authorization state."""

    has_discord: bool
    verified_email: str | None
    email_authorized: bool

    @property
    def has_verified_email(self) -> bool:
        return self.verified_email is not None

    @property
    def needs_ticket_email(self) -> bool:
        """A Discord-only user whose email is not in the validation API."""
        return self.has_discord and self.has_verified_email and not self.email_authorized


def _connection_status(request: HttpRequest, user: CustomUser) -> _ConnectionStatus:
    """Look up Discord membership, primary verified email, and API authorization."""
    has_discord = SocialAccount.objects.filter(user=user, provider=DISCORD_PROVIDER).exists()
    verified_email = (
        EmailAddress.objects.filter(user=user, verified=True)
        .order_by("-primary")
        .values_list("email", flat=True)
        .first()
    )
    email_authorized = False
    if verified_email:
        adapter = cast("AccountAdapter", get_adapter(request))
        email_authorized = adapter.can_login_by_email(verified_email)
    return _ConnectionStatus(
        has_discord=has_discord,
        verified_email=verified_email,
        email_authorized=email_authorized,
    )


# ---------------------------------------------------------------------------
# Connected accounts (wraps allauth's ConnectionsView with extra context)
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
def connections_view(request: HttpRequest) -> HttpResponse:
    """
    Show connected social accounts with awareness of whether disconnect is safe.

    Disconnect is only allowed when the user has a verified email that is also
    recognized by the validation API (i.e. associated with a ticket purchase).
    This prevents Discord-only staff users from disconnecting, changing their
    Discord email, and reconnecting to create unlimited accounts.
    """
    view = ConnectionsView.as_view()
    status = _connection_status(request, cast("CustomUser", request.user))

    # can_disconnect requires both a verified email AND validation API approval.
    can_disconnect = status.email_authorized
    if not status.has_verified_email:
        disconnect_blocked_reason = "no_verified_email"
    elif not status.email_authorized:
        disconnect_blocked_reason = "email_not_authorized"
    else:
        disconnect_blocked_reason = ""

    request.can_disconnect = can_disconnect  # type: ignore[attr-defined]
    request.has_discord = status.has_discord  # type: ignore[attr-defined]
    request.has_verified_email = status.has_verified_email  # type: ignore[attr-defined]
    request.disconnect_blocked_reason = disconnect_blocked_reason  # type: ignore[attr-defined]
    request.needs_ticket_email = status.needs_ticket_email  # type: ignore[attr-defined]
    request.verified_email = status.verified_email or ""  # type: ignore[attr-defined]
    return cast("HttpResponse", view(request))


# ---------------------------------------------------------------------------
# Add email address (for Discord-only users)
# ---------------------------------------------------------------------------

_ADD_EMAIL_SESSION_KEY = "_add_email"
_ADD_EMAIL_CODE_TIMEOUT = 300  # 5 minutes
_ADD_EMAIL_MAX_ATTEMPTS = 3
_ADD_EMAIL_TEMPLATE = "users/add_email.html"


@require_http_methods(["GET", "POST"])
def add_email_view(request: HttpRequest) -> HttpResponse:
    """
    Let a Discord-only user add and verify an email address.

    Step 1: accept an email, validate it against the authorization API, and send a code.
    The code, email, and expiry are stored in the session. On success the user is redirected
    to the confirmation page.
    """
    user = cast("CustomUser", request.user)

    # Allow through if the user needs to connect a ticket email (Discord user
    # whose current email is not recognized by the validation API).
    status = _connection_status(request, user)

    # Redirect unless this user actually needs to connect a ticket email
    if status.has_verified_email and not status.needs_ticket_email:
        return redirect("socialaccount_connections")

    events = Event.objects.filter(is_active=True).order_by("name")
    default_event_slug = getattr(settings, "DEFAULT_EVENT", "")
    error = ""

    def _render_form(email_value: str = "") -> HttpResponse:
        return render(
            request,
            _ADD_EMAIL_TEMPLATE,
            {
                "error": error,
                "events": events,
                "default_event_slug": default_event_slug,
                "email_value": email_value,
            },
        )

    if request.method == "POST":
        email = request.POST.get("email", "").lower().strip()
        event_slug = request.POST.get("event", "")

        # Basic format validation
        try:
            validate_email(email)
        except ValidationError:
            error = _("Please enter a valid email address.")
            return _render_form(email)

        # Check email is not already used by a different user
        UserModel = cast("type[CustomUser]", get_user_model())  # noqa: N806  # NOSONAR(S117)
        if UserModel.objects.filter(email=email).exclude(pk=user.pk).exists():
            error = _("This email address is already in use by another account.")
            return _render_form(email)

        # Validate via the authorization API (same as login)
        adapter = cast("AccountAdapter", get_adapter(request))
        event = (
            Event.objects.filter(slug=event_slug, is_active=True).first() if event_slug else None
        )
        adapter.set_selected_event(event)

        if not adapter.is_email_authorized(email):
            error = _("This email is not authorized for access.")
            return _render_form(email)

        # Generate code and store in session
        code = generate_user_code()
        expires_at = (timezone.now() + timedelta(seconds=_ADD_EMAIL_CODE_TIMEOUT)).isoformat()
        request.session[_ADD_EMAIL_SESSION_KEY] = {
            "email": email,
            "code": code,
            "expires": expires_at,
            "attempts": 0,
            "event_slug": event_slug,
        }

        # Send the code via email
        event_name = event.name if event else ""
        _send_add_email_code(email, code, event_name)

        logger.info("Add-email verification code sent", email=hash_email(email))
        return redirect("confirm_add_email")

    return _render_form()


@require_http_methods(["GET", "POST"])
def confirm_add_email_view(request: HttpRequest) -> HttpResponse:
    """
    Step 2: verify the code the user received by email.

    On success, create a verified ``EmailAddress`` record and update the user's primary email
    if it differs. Associate the user with the selected event.
    """
    user = cast("CustomUser", request.user)
    session_data: dict[str, Any] | None = request.session.get(_ADD_EMAIL_SESSION_KEY)

    if not session_data:
        return redirect("add_email")

    email = session_data["email"]
    error = ""

    if request.method == "POST":
        entered_code = request.POST.get("code", "").strip()

        # Check expiry
        expires = datetime.fromisoformat(session_data["expires"])
        if timezone.now() > expires:
            del request.session[_ADD_EMAIL_SESSION_KEY]
            messages.error(
                request,
                _("The verification code has expired. Please request a new one."),
                extra_tags="connections",
            )
            return redirect("add_email")

        # Check attempts
        session_data["attempts"] = session_data.get("attempts", 0) + 1
        request.session.modified = True

        if session_data["attempts"] > _ADD_EMAIL_MAX_ATTEMPTS:
            del request.session[_ADD_EMAIL_SESSION_KEY]
            messages.error(
                request,
                _("Too many incorrect attempts. Please request a new code."),
                extra_tags="connections",
            )
            return redirect("add_email")

        # Verify code (constant-time comparison, case-insensitive)
        if not secrets.compare_digest(entered_code.upper(), session_data["code"].upper()):
            error = _("The code you entered is incorrect. Please try again.")
            return render(
                request,
                "users/confirm_add_email.html",
                {
                    "email": obfuscate_email(email),
                    "error": error,
                },
            )

        # Code is valid - create the verified EmailAddress
        _finalize_add_email(user, email, session_data.get("event_slug", ""))
        del request.session[_ADD_EMAIL_SESSION_KEY]

        messages.success(
            request,
            _("Your email address has been verified successfully."),
            extra_tags="connections",
        )
        return redirect("socialaccount_connections")

    return render(
        request,
        "users/confirm_add_email.html",
        {
            "email": obfuscate_email(email),
            "error": error,
        },
    )


def _send_add_email_code(email: str, code: str, event_name: str) -> None:
    """Send the verification code to the given email address."""
    timeout_seconds = _ADD_EMAIL_CODE_TIMEOUT
    timeout_minutes = round(timeout_seconds / 60, 1)
    timeout_display = (
        int(timeout_minutes) if timeout_minutes == int(timeout_minutes) else timeout_minutes
    )
    prefix = f"{event_name} " if event_name else ""
    subject = _("%sEmail Verification Code") % prefix
    context = {
        "code": code,
        "brand_event_name": event_name,
        "timeout_minutes": timeout_display,
    }
    body = render_to_string("users/email/add_email_code.txt", context)
    html_body = render_to_string("users/email/add_email_code.html", context)
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        html_message=html_body,
    )


def _finalize_add_email(
    user: Any,
    email: str,
    event_slug: str,
) -> None:
    """Create verified EmailAddress, update user email if needed, link to event."""
    # Demote any existing primary emails for this user
    EmailAddress.objects.filter(user=user, primary=True).exclude(email__iexact=email).update(
        primary=False,
    )

    # Create or update the EmailAddress record
    email_obj, created = EmailAddress.objects.get_or_create(
        user=user,
        email__iexact=email,
        defaults={"email": email, "verified": True, "primary": True},
    )
    if not created:
        email_obj.verified = True
        email_obj.primary = True
        email_obj.save(update_fields=["verified", "primary"])

    # Update the user's email if it differs
    if user.email.lower() != email.lower():
        user.email = email
        user.save(update_fields=["email"])

    # Associate with the selected event
    if event_slug:
        event = Event.objects.filter(slug=event_slug, is_active=True).first()
        if event:
            user.events.add(event)

    logger.info("Email address verified and added", user_pk=user.pk, email=hash_email(email))
