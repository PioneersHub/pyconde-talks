"""Regression tests for security-sensitive Django settings."""

import pytest
from allauth.core.internal.ratelimit import parse_rates
from django.conf import settings


@pytest.mark.parametrize("action", ["login_failed", "request_login_code", "confirm_email"])
def test_auth_limits_are_keyed_per_account(action: str) -> None:
    """
    Every auth rate limit must carry a per-account/email bucket.

    ~2000 conference attendees share one venue NAT IP, so an auth limit that is *only* per-IP is
    collective punishment: one actor (or the opening-session login rush) would lock out everyone
    behind that IP. The per-account ("key") bucket is immune to a shared IP and is the protection
    actually wanted, so it must never be dropped.
    """
    scopes = {rate.per for rate in parse_rates(settings.ACCOUNT_RATE_LIMITS[action])}
    assert "key" in scopes, f"{action} lost its per-account rate limit"


@pytest.mark.parametrize("action", ["login_failed", "confirm_email"])
def test_account_scoped_actions_stay_off_the_shared_ip(action: str) -> None:
    """
    Actions that target a known account get no per-IP bucket at all.

    Their per-account key already bounds the abuse, so adding an IP bucket would only create the
    shared-venue lockout without buying anything.
    """
    scopes = {rate.per for rate in parse_rates(settings.ACCOUNT_RATE_LIMITS[action])}
    assert "ip" not in scopes, f"{action} must not be per-IP (a shared venue IP locks everyone out)"


def test_login_code_requests_carry_a_loose_ip_ceiling() -> None:
    """
    ``request_login_code`` is the one auth action that also needs a per-IP bound.

    Public events accept any address without a ticket check, and each new address creates a user
    row and sends mail, so a script walking a list of addresses never repeats the per-email key.
    The IP ceiling closes that, and is deliberately loose: it has to sit well above what the
    venue's shared NAT produces during the opening-session rush, so it makes bulk signup
    pointless without throttling real attendees.
    """
    limits = settings.ACCOUNT_RATE_LIMITS["request_login_code"]
    rates = {rate.per: rate for rate in parse_rates(limits)}
    assert "ip" in rates, "public-event registration needs a per-IP ceiling on login codes"
    per_ip = rates["ip"]
    assert per_ip.amount >= 50, "the per-IP ceiling must stay above venue-scale legitimate use"  # noqa: PLR2004


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
