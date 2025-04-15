"""Views for user authentication."""

from typing import Any, cast

import structlog
from allauth.account.adapter import get_adapter
from allauth.account.forms import LoginForm
from allauth.account.views import RequestLoginCodeView
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError
from django.http import HttpResponse

from pyconde_talks.utils.email_utils import hash_email


# Get logger
logger = structlog.get_logger(__name__)


class CustomRequestLoginCodeView(RequestLoginCodeView):
    """
    Custom view that overrides the default login code request process.

    This view checks if the email is authorized (whitelist, superuser or via API) before proceeding.
    """

    def form_valid(self, form: LoginForm) -> HttpResponse:
        """
        Check if the email is authorized before proceeding.

        If authorized but user doesn't exist, create the user.
        """
        email = form.cleaned_data["email"].lower()
        email_hash = hash_email(email)
        adapter = get_adapter(self.request)

        # Check if the email is authorized
        if not adapter.is_email_authorized(email):
            logger.warning("Unauthorized access attempt", email=email)
            form.add_error("email", "This email is not authorized for access.")
            return self.form_invalid(form)

        # If the email is authorized, create user if needed
        UserModel = get_user_model()  # noqa: N806
        if not UserModel.objects.filter(email=email).exists():
            try:
                logger.info("Creating new user account", email=email_hash)
                UserModel.objects.create_user(email=email, is_active=True)
                logger.info("Successfully created user account", email=email_hash)
            except (IntegrityError, ValidationError) as e:
                logger.warning("Failed to create user", email=email_hash, error=str(e))
                form.add_error(
                    "email",
                    "Unable to create account. Please ensure your email is valid.",
                )
                return self.form_invalid(form)
            except DatabaseError:
                logger.exception("Database error creating user", email=email_hash)
                form.add_error(
                    "email",
                    "System error while creating account. Please try again later.",
                )
                return self.form_invalid(form)
            except Exception:
                logger.exception("Unexpected error creating user", email=email_hash)
                form.add_error("email", "Error creating user. Please try again later.")
                return self.form_invalid(form)

        # Proceed with standard login code process
        return super().form_valid(form)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """
        Enhance the template context with login code timeout information.

        This method extends the parent context by adding the login code timeout value in minutes,
        calculated from settings.ACCOUNT_LOGIN_BY_CODE_TIMEOUT.
        """
        context = cast("dict[str, Any]", super().get_context_data(**kwargs))
        timeout_seconds = getattr(settings, "ACCOUNT_LOGIN_BY_CODE_TIMEOUT", 180)
        timeout_minutes = timeout_seconds / 60
        context["login_code_timeout_minutes"] = (
            int(timeout_minutes) if timeout_minutes.is_integer() else timeout_minutes
        )
        return context
