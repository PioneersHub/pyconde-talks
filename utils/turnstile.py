"""
Cloudflare Turnstile verification.

Entirely optional. ``is_enabled`` is False whenever either key is unset, and every call site
skips the check in that case, so local development, CI, and any deployment that would rather
not involve Cloudflare need no configuration at all.

https://developers.cloudflare.com/turnstile/
"""

from typing import Final

import httpx2
import structlog
from django.conf import settings


logger = structlog.get_logger(__name__)

# The POST parameter the Turnstile client script fills in. Fixed by Cloudflare.
RESPONSE_FIELD_NAME: Final = "cf-turnstile-response"


def is_enabled() -> bool:
    """Return whether both Turnstile keys are configured."""
    return bool(
        getattr(settings, "TURNSTILE_SITE_KEY", "")
        and getattr(settings, "TURNSTILE_SECRET_KEY", ""),
    )


def verify(token: str) -> bool:
    """
    Check a Turnstile token against Cloudflare, returning whether the challenge passed.

    Returns True when Turnstile is not configured, so callers do not need to ask first.

    On a network failure the answer follows ``TURNSTILE_FAIL_OPEN``, which defaults to letting
    the submission through: a Cloudflare outage taking the conference Q&A down with it is a
    worse outcome than a few minutes without a captcha.

    Deliberately not retried. Unlike the ticket-validation API, a Turnstile token is single-use
    and short-lived, so a retry after a timeout usually fails with ``timeout-or-duplicate`` and
    just delays the response.

    ``remoteip`` is omitted. It is optional, and behind a reverse proxy the wrong address is
    easy to send and causes false negatives; the token itself is the evidence that matters.
    """
    if not is_enabled():
        return True
    if not token:
        return False

    try:
        response = httpx2.post(
            settings.TURNSTILE_VERIFY_URL,
            data={"secret": settings.TURNSTILE_SECRET_KEY, "response": token},
            timeout=settings.TURNSTILE_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except httpx2.HTTPError:
        logger.warning(
            "Turnstile verification unreachable",
            fail_open=bool(settings.TURNSTILE_FAIL_OPEN),
        )
        return bool(settings.TURNSTILE_FAIL_OPEN)
    except ValueError:
        logger.warning("Turnstile returned a body that was not JSON")
        return bool(settings.TURNSTILE_FAIL_OPEN)

    if not data.get("success", False):
        # Cloudflare's error codes describe the token, not the person, so they are safe to log.
        logger.info("Turnstile challenge failed", error_codes=data.get("error-codes", []))
        return False
    return True
