"""Views for user authentication and profile management."""

from typing import TYPE_CHECKING, Any, cast

import structlog
from allauth.account.adapter import get_adapter
from allauth.account.internal import flows
from allauth.account.views import RequestLoginCodeView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render

from events.models import Event
from utils.email_utils import hash_email

from .forms import ProfileForm


if TYPE_CHECKING:
    from allauth.account.forms import LoginForm

    from .models import CustomUser


# Get logger
logger = structlog.get_logger(__name__)


class CustomRequestLoginCodeView(RequestLoginCodeView):  # type: ignore[misc]
    """
    Custom view that overrides the default login code request process.

    This view checks if the email is authorized (whitelist, superuser or via API) before proceeding.
    Supports event selection: the user picks which event they purchased a ticket for.
    """

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

        # Resolve the selected event
        event_slug = self.request.POST.get("event", "")
        event: Event | None = None
        if event_slug:
            event = Event.objects.filter(slug=event_slug, is_active=True).first()
        adapter.set_selected_event(event)

        # Check if the email is authorized
        if not adapter.is_email_authorized(email):
            logger.warning("Unauthorized access attempt", email=email)
            form.add_error("email", "This email is not authorized for access.")
            return cast("HttpResponse", self.form_invalid(form))

        # If the email is authorized, create user if needed
        UserModel = get_user_model()  # noqa: N806
        if not UserModel.objects.filter(email=email).exists():
            try:
                logger.info("Creating new user account", email=email_hash)
                user = UserModel.objects.create_user(email=email, is_active=True)  # type: ignore[attr-defined]
                # Associate the new user with the selected event
                if event:
                    user.events.add(event)
                form.user = user
                logger.info("Successfully created user account", email=email_hash, form=form.user)
                # Trigger the login code flow
                flows.login_by_code.LoginCodeVerificationProcess.initiate(
                    request=self.request,
                    user=user,
                    email=email,
                )
                # Redirect to success page
                return HttpResponseRedirect(self.get_success_url())
            except (IntegrityError, ValidationError) as exc:
                logger.warning("Failed to create user", email=email_hash, error=str(exc))
                form.add_error(
                    "email",
                    "Unable to create account. Please ensure your email is valid.",
                )
                return cast("HttpResponse", self.form_invalid(form))
            except DatabaseError:  # pragma: no cover
                logger.exception("Database error creating user", email=email_hash)
                form.add_error(
                    "email",
                    "System error while creating account. Please try again later.",
                )
                return cast("HttpResponse", self.form_invalid(form))
            except Exception:  # pragma: no cover
                logger.exception("Unexpected error creating user", email=email_hash)
                form.add_error("email", "Error creating user. Please try again later.")
                return cast("HttpResponse", self.form_invalid(form))

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


@login_required
def profile_view(request: HttpRequest) -> HttpResponse:
    """Allow the authenticated user to edit their profile information."""
    user = cast("CustomUser", request.user)
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile has been updated.", extra_tags="profile")
            return redirect("user_profile")
    else:
        form = ProfileForm(instance=user)

    return render(request, "users/profile.html", {"form": form})
