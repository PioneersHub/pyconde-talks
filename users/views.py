"""Views for user authentication and profile management."""

from typing import TYPE_CHECKING, Any, cast

import structlog
from allauth.account.adapter import get_adapter
from allauth.account.internal import flows
from allauth.account.views import RequestLoginCodeView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from events.models import Event
from utils.email_utils import hash_email, obfuscate_email

from .forms import ProfileForm


if TYPE_CHECKING:
    from allauth.account.forms import LoginForm

    from .models import CustomUser


# Get logger
logger = structlog.get_logger(__name__)


@login_not_required
class CustomRequestLoginCodeView(RequestLoginCodeView):  # type: ignore[misc]
    """
    Custom view that overrides the default login code request process.

    This view checks if the email is authorized (whitelist, superuser or via API) before proceeding.
    Supports event selection: the user picks which event they purchased a ticket for.
    """

    def _resolve_event(self) -> Event | None:
        """Resolve the selected event from POST data and persist it in the session."""
        event_slug = self.request.POST.get("event", "")
        event: Event | None = None
        if event_slug:
            event = Event.objects.filter(slug=event_slug, is_active=True).first()

        # Persist the selected event slug in the session so the context
        # processor can use it after login to resolve branding.
        if event and hasattr(self.request, "session"):
            self.request.session["selected_event_slug"] = event.slug

        return event

    def _create_new_user(self, email: str, event: Event | None, email_hash: str) -> HttpResponse:
        """Create a new user, link to event, and initiate login-code flow."""
        UserModel = get_user_model()  # noqa: N806  # NOSONAR(S117)
        logger.info("Creating new user account", email=email_hash)
        user = UserModel.objects.create_user(email=email, is_active=True)  # type: ignore[attr-defined]
        if event:
            user.events.add(event)
        logger.info("Successfully created user account", email=email_hash)
        flows.login_by_code.LoginCodeVerificationProcess.initiate(
            request=self.request,
            user=user,
            email=email,
        )
        return HttpResponseRedirect(self.get_success_url())

    def form_valid(self, form: LoginForm) -> HttpResponse:
        """
        Check if the email is authorized before proceeding.

        If authorized but user doesn't exist, create the user.
        """
        email = form.cleaned_data["email"].lower()

        email_hash = email
        if getattr(settings, "LOG_EMAIL_HASH", True):
            email_hash = hash_email(email)

        adapter = get_adapter(self.request)

        event = self._resolve_event()
        adapter.set_selected_event(event)

        # Check if the email is authorized
        if not adapter.is_email_authorized(email):
            logger.warning("Unauthorized access attempt", email=email)  # Not hashed
            form.add_error("email", _("This email is not authorized for access."))
            return cast("HttpResponse", self.form_invalid(form))

        # If the email is authorized, create user if needed
        UserModel = get_user_model()  # noqa: N806  # NOSONAR(S117)
        if not UserModel.objects.filter(email=email).exists():
            try:
                return self._create_new_user(email, event, email_hash)
            except (IntegrityError, ValidationError) as exc:
                logger.warning("Failed to create user", email=email_hash, error=str(exc))
                form.add_error(
                    "email",
                    _("Unable to create account. Please ensure your email is valid."),
                )
                return cast("HttpResponse", self.form_invalid(form))
            except DatabaseError:  # pragma: no cover
                logger.exception("Database error creating user", email=email_hash)
                form.add_error(
                    "email",
                    _("System error while creating account. Please try again later."),
                )
                return cast("HttpResponse", self.form_invalid(form))
            except Exception:  # pragma: no cover
                logger.exception("Unexpected error creating user", email=email_hash)
                form.add_error("email", _("Error creating user. Please try again later."))
                return cast("HttpResponse", self.form_invalid(form))

        # Associate existing user with the selected event
        if event:
            existing_user = cast("CustomUser | None", UserModel.objects.filter(email=email).first())
            if existing_user:
                existing_user.events.add(event)

        # Proceed with standard login code process
        logger.info("Form is valid", email=email_hash)
        return cast("HttpResponse", super().form_valid(form))

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """
        Enhance the template context with login code timeout and available events.

        This method extends the parent context by adding the login code timeout value in minutes,
        calculated from settings.ACCOUNT_LOGIN_BY_CODE_TIMEOUT, and the list of active events.
        """
        context = cast("dict[str, Any]", super().get_context_data(**kwargs))
        timeout_seconds = getattr(settings, "ACCOUNT_LOGIN_BY_CODE_TIMEOUT", 180)
        timeout_minutes = timeout_seconds / 60
        context["login_code_timeout_minutes"] = (
            int(timeout_minutes) if timeout_minutes.is_integer() else timeout_minutes
        )
        # Provide available events and the default selection
        context["events"] = Event.objects.filter(is_active=True).order_by("name")
        context["default_event_slug"] = getattr(settings, "DEFAULT_EVENT", "")
        return context


def profile_view(request: HttpRequest) -> HttpResponse:
    """Allow the authenticated user to edit their profile information."""
    user = cast("CustomUser", request.user)
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Your profile has been updated."), extra_tags="profile")
            return redirect("user_profile")
    else:
        form = ProfileForm(instance=user)

    qa_display_name = (
        user.display_name.strip()
        or user.get_full_name().strip()
        or obfuscate_email(user.email)
        or _("Anonymous")
    )
    return render(request, "users/profile.html", {"form": form, "qa_display_name": qa_display_name})
