"""
Authentication-related signal handlers that log to the dedicated 'auth' logger.

This module listens to Django's auth signals and emits structured JSON logs to a dedicated log file
configured via the "auth" logger.
"""

from __future__ import annotations

import hashlib
from typing import Any

import structlog
from django.conf import settings
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver


logger = structlog.get_logger("auth")


def _hash_or_plain(value: str | None) -> str | None:
    """Hash sensitive values if LOG_EMAIL_HASH is enabled, otherwise return as-is."""
    if not value:
        return value
    if getattr(settings, "LOG_EMAIL_HASH", True):
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    return value


def _client_ip(request: Any | None) -> str | None:
    if request is None:
        return None
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        # take first IP in list
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@receiver(user_logged_in)
def on_user_logged_in(
    sender: type[Any],
    request: Any,
    user: Any,
    **_kwargs: Any,
) -> None:
    """Log successful user login."""
    del sender, _kwargs
    logger.info(
        "login",
        user_id=getattr(user, "pk", None),
        user_email=_hash_or_plain(getattr(user, "email", None)),
        ip=_client_ip(request),
    )


@receiver(user_logged_out)
def on_user_logged_out(
    sender: type[Any],
    request: Any,
    user: Any | None,
    **_kwargs: Any,
) -> None:
    """Log user logout."""
    del sender, _kwargs
    logger.info(
        "logout",
        user_id=getattr(user, "pk", None) if user else None,
        ip=_client_ip(request),
    )


@receiver(user_login_failed)
def on_user_login_failed(
    sender: type[Any],
    credentials: dict[str, Any],
    request: Any | None = None,
    **_kwargs: Any,
) -> None:  # type: ignore[override]
    """Log failed login attempts without sensitive data."""
    del sender, _kwargs
    # Make a safe copy of credentials without password
    safe_credentials: dict[str, Any] = {}
    for key, value in (credentials or {}).items():
        if key.lower() == "password":
            continue
        if key.lower() in {"email", "username"}:
            # Hash potentially sensitive identifiers
            safe_credentials[key] = _hash_or_plain(str(value))
        else:
            safe_credentials[key] = value

    logger.warning(
        "login_failed",
        provided=safe_credentials,
        ip=_client_ip(request),
    )
