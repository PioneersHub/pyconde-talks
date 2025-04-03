"""Custom adapter for django-allauth that validates e-mails using an external API."""

import logging
from json.decoder import JSONDecodeError

import requests
from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
    RequestException,
    Timeout,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from pyconde_talks.utils.email_utils import hash_email


logger = logging.getLogger("users.adapters")


class AccountAdapter(DefaultAccountAdapter):
    """
    Custom adapter for django-allauth that validates emails using an external API.

    This adapter implements a multi-layered authorization strategy:
    1. Local authorization (whitelist, superusers, and active users)
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
        email_hash = hash_email(email)

        # Check if email is in the whitelist
        if email in getattr(settings, "AUTHORIZED_EMAILS_WHITELIST", []):
            return True

        # Check if this email belongs to an administrator or active user
        # Note: admins can also login from /admin/login/ using their password
        UserModel = get_user_model()  # noqa: N806
        try:
            user = UserModel.objects.get(email=email)
            if user.is_superuser:
                logger.info("Admin authorized: %s", email_hash)
                return True
            if user.is_active:
                logger.info("User authorized: %s", email_hash)
                return True
        except UserModel.DoesNotExist:
            pass

        # Check the API
        is_valid = False
        try:
            data = self._call_validation_api(email)
            is_valid = data.get("valid", False)

            if is_valid:
                logger.info("Successfully validated email: %s", email_hash)
            else:
                logger.warning("Email validation failed for: %s", email_hash)

        except Timeout:
            logger.warning("Timeout validating email: %s", email_hash)
        except RequestsConnectionError:
            logger.warning("Connection error validating email: %s", email_hash)
        except JSONDecodeError:
            logger.warning("Invalid JSON response validating email: %s", email_hash)
        except RequestException as e:
            logger.warning("Request error validating email: %s. Error: %s", email_hash, str(e))
        except Exception:
            logger.exception("Unexpected error validating email: %s", email_hash)

        return is_valid

    @staticmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((Timeout, RequestsConnectionError)),
        reraise=True,
    )
    def _call_validation_api(email: str) -> dict:
        """Call the validation API with retry."""
        response = requests.post(
            settings.EMAIL_VALIDATION_API_URL,
            json={"email": email},
            timeout=getattr(settings, "EMAIL_VALIDATION_API_TIMEOUT", 5),
        )
        response.raise_for_status()
        return response.json()
