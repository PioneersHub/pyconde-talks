"""
Tests for the Cloudflare Turnstile helper.

The behaviour worth pinning is what happens when Cloudflare is unreachable, and that a deployment
without keys makes no network call at all.
"""

from typing import TYPE_CHECKING

import httpx2
import pytest

from utils import turnstile


if TYPE_CHECKING:
    import respx
    from pytest_django.fixtures import SettingsWrapper


VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

# Two tests register a route precisely to assert it is never called, so the default
# "every route must be used" teardown check does not apply here.
pytestmark = pytest.mark.httpx2(assert_all_called=False)


@pytest.fixture
def _configured(settings: SettingsWrapper) -> None:
    """Configure Turnstile with test keys."""
    settings.TURNSTILE_SITE_KEY = "site-key"
    settings.TURNSTILE_SECRET_KEY = "secret-key"
    settings.TURNSTILE_VERIFY_URL = VERIFY_URL
    settings.TURNSTILE_TIMEOUT = 5
    settings.TURNSTILE_FAIL_OPEN = True


@pytest.fixture
def _no_keys(settings: SettingsWrapper) -> None:
    """Leave Turnstile switched off, as in dev and CI."""
    settings.TURNSTILE_SITE_KEY = ""
    settings.TURNSTILE_SECRET_KEY = ""


@pytest.mark.parametrize(
    ("site_key", "secret_key", "expected"),
    [
        ("", "", False),
        ("site-key", "", False),
        ("", "secret-key", False),
        ("site-key", "secret-key", True),
    ],
)
def test_is_enabled_needs_both_keys(
    settings: SettingsWrapper,
    site_key: str,
    secret_key: str,
    expected: bool,  # noqa: FBT001
) -> None:
    """Half a configuration is not a configuration."""
    settings.TURNSTILE_SITE_KEY = site_key
    settings.TURNSTILE_SECRET_KEY = secret_key
    assert turnstile.is_enabled() is expected


@pytest.mark.usefixtures("_no_keys")
def test_no_keys_passes_without_calling_out(httpx2_mock: respx.Router) -> None:
    """
    A deployment without keys must not reach for the network at all.

    This is what lets dev and CI run with no Turnstile setup and no mocking.
    """
    route = httpx2_mock.post(VERIFY_URL)
    assert turnstile.verify("anything") is True
    assert route.called is False


@pytest.mark.usefixtures("_configured")
def test_missing_token_fails_without_calling_out(httpx2_mock: respx.Router) -> None:
    """No token means no challenge was completed; there is nothing to ask Cloudflare about."""
    route = httpx2_mock.post(VERIFY_URL)
    assert turnstile.verify("") is False
    assert route.called is False


@pytest.mark.usefixtures("_configured")
def test_successful_challenge(httpx2_mock: respx.Router) -> None:
    """Cloudflare says the token is good, so the submission goes ahead."""
    httpx2_mock.post(VERIFY_URL).respond(json={"success": True})
    assert turnstile.verify("a-token") is True


@pytest.mark.usefixtures("_configured")
def test_failed_challenge(httpx2_mock: respx.Router) -> None:
    """Cloudflare rejects the token, so the submission is refused."""
    httpx2_mock.post(VERIFY_URL).respond(
        json={"success": False, "error-codes": ["invalid-input-response"]},
    )
    assert turnstile.verify("a-token") is False


@pytest.mark.parametrize("fail_open", [True, False])
def test_network_failure_follows_fail_open(
    settings: SettingsWrapper,
    httpx2_mock: respx.Router,
    fail_open: bool,  # noqa: FBT001
) -> None:
    """
    A Cloudflare outage is a policy decision, not a crash.

    The default is to let submissions through: losing the captcha for a few minutes beats attendees
    being unable to ask anything at all.
    """
    settings.TURNSTILE_SITE_KEY = "site-key"
    settings.TURNSTILE_SECRET_KEY = "secret-key"
    settings.TURNSTILE_VERIFY_URL = VERIFY_URL
    settings.TURNSTILE_TIMEOUT = 5
    settings.TURNSTILE_FAIL_OPEN = fail_open

    httpx2_mock.post(VERIFY_URL).mock(side_effect=httpx2.ConnectError("unreachable"))
    assert turnstile.verify("a-token") is fail_open


@pytest.mark.usefixtures("_configured")
def test_a_non_json_body_is_treated_as_an_outage(httpx2_mock: respx.Router) -> None:
    """An error page from a proxy is an outage in disguise, not a failed challenge."""
    httpx2_mock.post(VERIFY_URL).respond(text="<html>502 Bad Gateway</html>")
    assert turnstile.verify("a-token") is True


@pytest.mark.usefixtures("_configured")
def test_an_http_error_is_treated_as_an_outage(httpx2_mock: respx.Router) -> None:
    """A 500 from Cloudflare is their problem, and follows the same fail-open policy."""
    httpx2_mock.post(VERIFY_URL).respond(status_code=500)
    assert turnstile.verify("a-token") is True


@pytest.mark.usefixtures("_configured")
def test_the_token_is_sent_but_the_client_ip_is_not(httpx2_mock: respx.Router) -> None:
    """
    The client IP is deliberately omitted.

    It is optional, and behind a reverse proxy the wrong address is easy to send and causes false
    negatives. The token is the evidence that matters.
    """
    httpx2_mock.post(VERIFY_URL).respond(json={"success": True})
    turnstile.verify("a-token")

    body = httpx2_mock.calls[0].request.content.decode()
    assert "response=a-token" in body
    assert "remoteip" not in body


@pytest.mark.parametrize(
    "code",
    ["invalid-input-secret", "missing-input-secret", "bad-request", "internal-error"],
)
@pytest.mark.parametrize("fail_open", [True, False])
def test_a_bad_secret_follows_fail_open(
    settings: SettingsWrapper,
    httpx2_mock: respx.Router,
    code: str,
    fail_open: bool,  # noqa: FBT001
) -> None:
    """
    A wrong secret is our mistake, not evidence of a bot.

    It looks the same as a rejected token in the response body, so without this the whole Q&A
    silently refuses every question until an operator works out why. Treated like an outage, which
    is what it is from an attendee's point of view.
    """
    settings.TURNSTILE_SITE_KEY = "site-key"
    settings.TURNSTILE_SECRET_KEY = "a-placeholder-that-was-never-replaced"
    settings.TURNSTILE_VERIFY_URL = VERIFY_URL
    settings.TURNSTILE_TIMEOUT = 5
    settings.TURNSTILE_FAIL_OPEN = fail_open

    httpx2_mock.post(VERIFY_URL).respond(json={"success": False, "error-codes": [code]})
    assert turnstile.verify("a-token") is fail_open


@pytest.mark.usefixtures("_configured")
def test_a_rejected_token_still_fails_closed_when_fail_open_is_on(
    settings: SettingsWrapper,
    httpx2_mock: respx.Router,
) -> None:
    """
    Fail-open covers our problems, not the visitor's.

    A token Cloudflare rejects is the captcha doing its job, so it must be refused even while
    TURNSTILE_FAIL_OPEN is set, or the setting would switch the check off altogether.
    """
    settings.TURNSTILE_FAIL_OPEN = True
    httpx2_mock.post(VERIFY_URL).respond(
        json={"success": False, "error-codes": ["invalid-input-response"]},
    )
    assert turnstile.verify("a-token") is False


@pytest.mark.usefixtures("_configured")
def test_a_duplicate_token_fails_closed(httpx2_mock: respx.Router) -> None:
    """A replayed token is a client-side problem, so it is refused rather than waved through."""
    httpx2_mock.post(VERIFY_URL).respond(
        json={"success": False, "error-codes": ["timeout-or-duplicate"]},
    )
    assert turnstile.verify("a-token") is False
