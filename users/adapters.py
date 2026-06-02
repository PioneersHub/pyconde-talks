"""
Custom account adapter for django-allauth (e-mail validation).

The Discord social-login adapter lives in ``users.adapters_social``.
"""

from http import HTTPStatus
from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any, cast, override

import httpx
import structlog
from allauth.account.adapter import (
    DefaultAccountAdapter,
)
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from events.models import Event
from utils.email_utils import hash_email


if TYPE_CHECKING:
    from users.models import CustomUser

# Re-export so settings.py's "users.adapters.SocialAccountAdapter" still resolves.
from .adapters_social import SocialAccountAdapter  # noqa: F401


logger = structlog.get_logger(__name__)

# Safety margin (seconds) subtracted from the token's expires_in so we refresh before expiry.
_TOKEN_EXPIRY_MARGIN = 30
# Default TTL when the token endpoint omits ``expires_in`` (5 min, matching most OIDC defaults).
_TOKEN_DEFAULT_TTL = 300
# Versioned cache key so a future change to the stored payload format does not collide with stale
# values that may still be in Redis from the previous deploy.
OAUTH_BEARER_CACHE_KEY = "users.adapters.oauth_bearer:v1"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
    reraise=True,
)
def _fetch_oauth_token(client_id: str, client_secret: str, token_url: str) -> tuple[str, int]:
    """
    Exchange client credentials for an access token, returning ``(token, ttl_seconds)``.

    The TTL is ``expires_in`` minus a safety margin so callers refresh before real expiry.
    """
    response = httpx.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=getattr(settings, "EMAIL_VALIDATION_API_TIMEOUT", 10),
    )
    response.raise_for_status()
    data = response.json()
    access_token: str = data["access_token"]
    expires_in: int = data.get("expires_in", _TOKEN_DEFAULT_TTL)
    ttl = max(expires_in - _TOKEN_EXPIRY_MARGIN, 0)
    return access_token, ttl


def _get_oauth_token() -> str | None:
    """
    Return a valid Bearer token, or ``None`` if OAuth2 is not configured.

    The token is stored in Django's cache (``django.core.cache``), so all worker
    processes share it - with Redis/memcached as the prod backend, one fetch per
    cluster-wide expiry instead of one per worker. The ``timeout`` parameter on
    ``cache.set`` handles eviction; no manual expiry tracking needed here.
    """
    client_id = getattr(settings, "EMAIL_VALIDATION_API_OAUTH2_CLIENT_ID", "")
    client_secret = getattr(settings, "EMAIL_VALIDATION_API_OAUTH2_CLIENT_SECRET", "")
    token_url = getattr(settings, "EMAIL_VALIDATION_API_OAUTH2_TOKEN_URL", "")

    if not (client_id and client_secret and token_url):
        return None

    cached: str | None = cache.get(OAUTH_BEARER_CACHE_KEY)
    if cached:
        return cached

    # Two workers can race to fetch on cold start; both will receive valid tokens from the IdP
    # and the second cache.set just overwrites with an equivalent value. Acceptable.
    token, ttl = _fetch_oauth_token(client_id, client_secret, token_url)
    if ttl > 0:
        cache.set(OAUTH_BEARER_CACHE_KEY, token, timeout=ttl)
    return token


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
        except DatabaseError:  # pragma: no cover
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
        except DatabaseError:  # pragma: no cover
            logger.exception("Database error checking privileged status", email=email_hash)
        return False

    def _safe_call_validation_api(
        self,
        email: str,
        api_url: str,
        email_hash: str,
        context_msg: str,
    ) -> dict[str, Any] | None:
        """Call the validation API, returning ``None`` (and logging) on any error."""
        try:
            return self._call_validation_api(email, api_url)
        except httpx.TimeoutException:  # pragma: no cover
            logger.warning("Timeout %s", context_msg, email=email_hash)
        except httpx.ConnectError:  # pragma: no cover
            logger.warning("Connection error %s", context_msg, email=email_hash)
        except JSONDecodeError, httpx.HTTPError:  # pragma: no cover
            logger.warning("API error %s", context_msg, email=email_hash)
        except Exception:  # pragma: no cover
            logger.exception("Unexpected error %s", context_msg, email=email_hash)
        return None

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

        data = self._safe_call_validation_api(email, api_url, email_hash, "validating email")
        is_valid = bool(data and data.get("valid", False))
        if is_valid:
            logger.info("Successfully validated email", email=email_hash)
        else:
            logger.warning("Email validation failed", email=email_hash)
        return is_valid

    @staticmethod
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    def _call_validation_api(email: str, api_url: str) -> dict[str, Any]:
        """
        Call the validation API with retry and optional OAuth2 Bearer token.

        Returns ``{"valid": False}`` immediately when ``api_url`` is empty or when the API responds
        with 404 (meaning the email is not registered in the system).
        Only transient network failures (timeouts, connection errors) trigger a retry.
        """
        if not api_url:
            return {"valid": False}

        headers: dict[str, str] = {}
        token = _get_oauth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = httpx.post(
            api_url,
            json={"email": email},
            headers=headers,
            timeout=getattr(settings, "EMAIL_VALIDATION_API_TIMEOUT", 5),
        )
        # 404 means the email is not registered. Return early so tenacity does not retry.
        if response.status_code == HTTPStatus.NOT_FOUND:
            return {"valid": False}
        response.raise_for_status()
        return dict(response.json())

    def can_login_by_email(self, email: str) -> bool:
        """
        Check if the email can be used for passwordless code-based login.

        Determines whether an email would pass authorization when the user attempts to log in via
        the email code flow. Used to decide whether disconnecting a social provider is safe (the
        user can still sign in).

        Unlike ``is_email_authorized()``, this method has no side effects and does not require an
        event selection. It checks the email against every active event validation API (deduplicated
        by URL) plus the fallback.

        Returns True for whitelisted emails, superuser accounts, or emails recognized by any
        configured validation API.
        """
        email = email.lower().strip()
        email_hash = hash_email(email)

        if self._is_privileged(email, email_hash):
            return True

        # Collect unique validation API URLs from active events + fallback.
        api_urls: set[str] = set(
            Event.objects.filter(is_active=True)
            .exclude(validation_api_url="")
            .values_list("validation_api_url", flat=True),
        )
        fallback_url = getattr(settings, "EMAIL_VALIDATION_API_URL_FALLBACK", "")
        if fallback_url:
            api_urls.add(fallback_url)

        if not api_urls:
            logger.info(
                "No validation API configured; email login not viable",
                email=email_hash,
            )
            return False

        for api_url in api_urls:
            data = self._safe_call_validation_api(
                email,
                api_url,
                email_hash,
                "checking email login viability",
            )
            if data and data.get("valid", False):
                logger.info("Email validated for independent login", email=email_hash)
                return True

        return False

    @override
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
