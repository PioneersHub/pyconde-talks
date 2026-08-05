"""
Cloudflare Turnstile verification.

Entirely optional. ``is_enabled`` is False whenever either key is unset, and every call site skips
the check in that case, so local development, CI, and any deployment that would rather not involve
Cloudflare need no configuration at all.

https://developers.cloudflare.com/turnstile/
"""

from typing import Any, Final

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

    On a network failure the answer follows ``TURNSTILE_FAIL_OPEN``, which defaults to letting the
    submission through: a Cloudflare outage taking the conference Q&A down with it is a worse
    outcome than a few minutes without a captcha.

    Deliberately not retried. Unlike the ticket-validation API, a Turnstile token is single-use and
    short-lived, so a retry after a timeout usually fails with ``timeout-or-duplicate`` and just
    delays the response.

    ``remoteip`` is omitted. It is optional, and behind a reverse proxy the wrong address is easy to
    send and causes false negatives; the token itself is the evidence that matters.
    """
    if not is_enabled():
        return True
    if not token:
        return False

    verdict = _fetch_verdict(token)
    if verdict is None:
        # No usable answer came back at all, so this is the outage case.
        return bool(settings.TURNSTILE_FAIL_OPEN)
    return _allows(verdict)


def _fetch_verdict(token: str) -> dict[str, Any] | None:
    """
    POST *token* to Cloudflare and return the decoded body, or None if it could not be read.

    None covers both an unreachable endpoint and a body that is not JSON. Both mean the same thing
    to the caller: there is no verdict, so policy has to decide.
    """
    try:
        response = httpx2.post(
            settings.TURNSTILE_VERIFY_URL,
            data={"secret": settings.TURNSTILE_SECRET_KEY, "response": token},
            timeout=settings.TURNSTILE_TIMEOUT,
        )
        response.raise_for_status()
        # ``json()`` is untyped, and a well-formed body that is not an object (a bare list, say)
        # would break every ``.get`` below, so narrow it here rather than at each use.
        decoded: object = response.json()
        return decoded if isinstance(decoded, dict) else None
    except httpx2.HTTPError:
        logger.warning(
            "Turnstile verification unreachable",
            fail_open=bool(settings.TURNSTILE_FAIL_OPEN),
        )
        return None
    except ValueError:
        logger.warning("Turnstile returned a body that was not JSON")
        return None


def _allows(verdict: dict[str, Any]) -> bool:
    """
    Turn Cloudflare's verdict into an allow or a deny.

    A rejected token and a rejected secret come back in the same shape, so they are separated here:
    the first is the captcha working, the second is our misconfiguration.
    """
    if verdict.get("success", False):
        return True

    # Cloudflare's error codes describe the token, not the person, so they are safe to log.
    codes: list[str] = verdict.get("error-codes") or []
    if _is_our_fault(codes):
        # A wrong or missing secret rejects every question on the site until someone notices.
        # Treat it like an outage and follow TURNSTILE_FAIL_OPEN, but log at error level:
        # unlike an outage, this one does not fix itself.
        logger.error("Turnstile is misconfigured", error_codes=codes)
        return bool(settings.TURNSTILE_FAIL_OPEN)

    logger.info("Turnstile challenge failed", error_codes=codes)
    return False


# Codes that say the request we sent was wrong, rather than that the token was. A bad secret
# looks identical to a bad token in the response shape, so the distinction has to be made here.
# https://developers.cloudflare.com/turnstile/get-started/server-side-validation/#error-codes
_CONFIGURATION_ERROR_CODES: Final = frozenset(
    {
        "missing-input-secret",
        "invalid-input-secret",
        "bad-request",
        "internal-error",
    },
)


def _is_our_fault(codes: list[str]) -> bool:
    """Return whether *codes* point at our configuration rather than at the submitted token."""
    return any(code in _CONFIGURATION_ERROR_CODES for code in codes)
