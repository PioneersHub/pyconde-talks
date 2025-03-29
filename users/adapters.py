"""Custom adapter for django-allauth that validates e-mails using an external API."""

import requests
from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model


class CustomAccountAdapter(DefaultAccountAdapter):
    """
    Custom adapter for django-allauth that validates emails using an external API.

    This adapter implements a multi-layered authorization strategy:
    1. Local authorization (whitelist and superusers)
    2. External API authorization

    Configuration is done via Django settings:
    - EMAIL_VALIDATION_API_URL: URL of the validation API
    - EMAIL_VALIDATION_API_TIMEOUT: Timeout for API requests in seconds
    - AUTHORIZED_EMAILS_WHITELIST: List of pre-authorized emails
    """

    def is_email_authorized(self, email: str) -> bool:
        """
        Validate if email is authorized for login.

        First, check if the email is in the whitelist or belongs to a superuser.
        If not, send the email to an external API for validation.

        Args:
            email: The email address to validate

        Returns:
            bool: True if the email is authorized, False otherwise (including on API errors)

        """
        # Normalize email (the API should be case-insensitive)
        email = email.lower().strip()

        # Check if email is in the whitelist
        if email in getattr(settings, "AUTHORIZED_EMAILS_WHITELIST", []):
            return True

        # Check if this is a superuser email
        # Note: admins can also login from /admin/login/ using their password
        UserModel = get_user_model()  # noqa: N806
        try:
            user = UserModel.objects.get(email=email)
            if user.is_superuser:
                return True
        except UserModel.DoesNotExist:
            pass

        # Check the API
        try:
            response = requests.post(
                settings.EMAIL_VALIDATION_API_URL,
                json={"email": email},
                timeout=settings.EMAIL_VALIDATION_API_TIMEOUT,
            )

            HTTP_STATUS_OK = 200  # noqa: N806
            if response.status_code == HTTP_STATUS_OK:
                data = response.json()
                return data.get("valid", False)
            return False
        except Exception:
            return False
