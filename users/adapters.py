"""Custom adapter for django-allauth that validates e-mails using an external API."""

from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog
from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DatabaseError, OperationalError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from utils.email_utils import hash_email


if TYPE_CHECKING:
    from events.models import Event
    from users.models import CustomUser


logger = structlog.get_logger(__name__)


class AccountAdapter(DefaultAccountAdapter):  # type: ignore[misc]
    """
    Custom adapter for django-allauth that validates emails using an external API.

    This adapter implements a multi-layered, event-aware authorization strategy:
    1. Superusers and whitelisted emails are always authorized.
    2. If the user already exists and is associated with the selected event, let them in.
    3. If the user exists but is NOT associated with the selected event, call the event's
       validation API. If valid, associate the user with the event.
    4. If the user does not exist, call the event's validation API. If valid, the user will be
       created and associated with the event.

    The selected event is passed via the login form's ``event`` field and stored on the adapter
    as ``self._selected_event``.
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
        UserModel = cast("type[CustomUser]", get_user_model())  # noqa: N806
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

        UserModel = cast("type[CustomUser]", get_user_model())  # noqa: N806
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
