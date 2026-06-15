"""Regression tests for security-sensitive Django settings."""

import pytest
from allauth.core.internal.ratelimit import parse_rates
from django.conf import settings


@pytest.mark.parametrize("action", ["login_failed", "request_login_code", "confirm_email"])
def test_auth_limits_are_keyed_per_account_not_per_ip(action: str) -> None:
    """
    Auth rate limits must be keyed per account/email, never per IP.

    ~2000 conference attendees share one venue NAT IP, so a per-IP auth limit is collective
    punishment: one actor (or the opening-session login rush) would lock out everyone behind that
    IP. Per-account ("key") limits are immune to a shared IP. This fails loudly if a per-IP bucket
    creeps back into an auth limit.
    """
    scopes = {rate.per for rate in parse_rates(settings.ACCOUNT_RATE_LIMITS[action])}
    assert "key" in scopes, f"{action} lost its per-account rate limit"
    assert "ip" not in scopes, f"{action} must not be per-IP (a shared venue IP locks everyone out)"


@pytest.mark.parametrize("action", ["login", "signup"])
def test_anonymous_ip_only_limits_are_disabled(action: str) -> None:
    """
    login/signup have no per-account key, so their per-IP default is disabled.

    Leaving them per-IP would let one attendee lock the whole venue out of logging in or signing
    up. Brute force stays bounded by the per-account limits, the passwordless email-code flow, and
    the email-validation gate.
    """
    assert not parse_rates(settings.ACCOUNT_RATE_LIMITS[action])


def test_trusted_proxy_count_is_configured() -> None:
    """ALLAUTH_TRUSTED_PROXY_COUNT must exist so allauth can derive the real client IP."""
    assert isinstance(settings.ALLAUTH_TRUSTED_PROXY_COUNT, int)
    assert settings.ALLAUTH_TRUSTED_PROXY_COUNT >= 0
