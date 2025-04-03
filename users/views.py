"""Views for user authentication."""

import logging

from allauth.account.adapter import get_adapter
from allauth.account.forms import LoginForm
from allauth.account.views import RequestLoginCodeView
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError
from django.http import HttpResponse

from pyconde_talks.utils.email_utils import hash_email


# Get logger
logger = logging.getLogger(__name__)


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
            logger.warning("Unauthorized access attempt from email: %s", email_hash)
            form.add_error("email", "This email is not authorized for access.")
            return self.form_invalid(form)

        # If the email is authorized, create user if needed
        UserModel = get_user_model()  # noqa: N806
        if not UserModel.objects.filter(email=email).exists():
            try:
                logger.info("Creating new user account for email: %s", email_hash)
                UserModel.objects.create_user(email=email, is_active=True)
                logger.info("Successfully created user account for: %s", email_hash)
            except (IntegrityError, ValidationError) as e:
                logger.warning("Failed to create user for %s: %s", email_hash, str(e))
                form.add_error(
                    "email",
                    "Unable to create account. Please ensure your email is valid.",
                )
                return self.form_invalid(form)
            except DatabaseError:
                logger.exception("Database error creating user for %s", email_hash)
                form.add_error(
                    "email",
                    "System error while creating account. Please try again later.",
                )
                return self.form_invalid(form)
            except Exception:
                logger.exception("Unexpected error creating user for %s", email_hash)
                form.add_error("email", "Error creating user. Please try again later.")
                return self.form_invalid(form)

        # Proceed with standard login code process
        return super().form_valid(form)
