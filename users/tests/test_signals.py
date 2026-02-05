"""Tests for users.signals covering auth signal handlers."""
# ruff: noqa: PLR2004

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from model_bakery import baker

from users.models import CustomUser
from users.signals import (
    _client_ip,
    _hash_or_plain,
    on_user_logged_in,
    on_user_logged_out,
    on_user_login_failed,
)


class TestHashOrPlain:
    """Verify _hash_or_plain hashes or passes through emails based on settings."""

    @override_settings(LOG_EMAIL_HASH=True)
    def test_hash_enabled(self) -> None:
        """Return a SHA-256 hex digest when LOG_EMAIL_HASH is enabled."""
        result = _hash_or_plain("test@example.com")
        assert result is not None
        assert result != "test@example.com"
        assert len(result) == 64  # SHA-256 hex digest

    @override_settings(LOG_EMAIL_HASH=False)
    def test_hash_disabled(self) -> None:
        """Return the email in plain text when LOG_EMAIL_HASH is disabled."""
        result = _hash_or_plain("test@example.com")
        assert result == "test@example.com"

    def test_none_value(self) -> None:
        """Return None when the input value is None."""
        assert _hash_or_plain(None) is None

    def test_empty_string(self) -> None:
        """Return an empty string when the input is empty."""
        assert _hash_or_plain("") == ""


class TestClientIp:
    """Verify _client_ip extracts the client IP from various request headers."""

    def test_none_request(self) -> None:
        """Return None when the request is None."""
        assert _client_ip(None) is None

    def test_xff_header(self) -> None:
        """Extract the first IP from the X-Forwarded-For header."""
        request = MagicMock()
        request.META = {"HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8"}
        assert _client_ip(request) == "1.2.3.4"

    def test_remote_addr(self) -> None:
        """Fall back to REMOTE_ADDR when no X-Forwarded-For header is present."""
        request = MagicMock()
        request.META = {"REMOTE_ADDR": "10.0.0.1"}
        assert _client_ip(request) == "10.0.0.1"

    def test_no_addr(self) -> None:
        """Return None when no address headers are present."""
        request = MagicMock()
        request.META = {}
        assert _client_ip(request) is None


@pytest.mark.django_db
class TestSignalHandlers:
    """Verify signal handlers log authentication events correctly."""

    @patch("users.signals.logger")
    def test_on_user_logged_in(self, mock_logger: MagicMock) -> None:
        """Log an info message when a user successfully logs in."""
        user = baker.make(CustomUser, email="login@example.com")
        request = MagicMock()
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        on_user_logged_in(sender=CustomUser, request=request, user=user)
        mock_logger.info.assert_called_once()

    @patch("users.signals.logger")
    def test_on_user_logged_out(self, mock_logger: MagicMock) -> None:
        """Log an info message when a user logs out."""
        user = baker.make(CustomUser, email="logout@example.com")
        request = MagicMock()
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        on_user_logged_out(sender=CustomUser, request=request, user=user)
        mock_logger.info.assert_called_once()

    @patch("users.signals.logger")
    def test_on_user_logged_out_no_user(self, mock_logger: MagicMock) -> None:
        """Log an info message even when user is None (session-only logout)."""
        request = MagicMock()
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        on_user_logged_out(sender=CustomUser, request=request, user=None)
        mock_logger.info.assert_called_once()

    @patch("users.signals.logger")
    def test_on_user_login_failed(self, mock_logger: MagicMock) -> None:
        """Log a warning with sanitized credentials, stripping sensitive keys like password."""
        request = MagicMock()
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        on_user_login_failed(
            sender=CustomUser,
            credentials={
                "email": "bad@example.com",
                "password": "secret",
                "backend": "django.contrib.auth.backends.ModelBackend",
            },
            request=request,
        )
        mock_logger.warning.assert_called_once()
        call_kwargs = mock_logger.warning.call_args[1]
        # "backend" is a non-sensitive key, should be passed through as-is
        assert call_kwargs["provided"]["backend"] == "django.contrib.auth.backends.ModelBackend"
        # "password" should NOT be in the provided dict
        assert "password" not in call_kwargs["provided"]

    @patch("users.signals.logger")
    def test_on_user_login_failed_no_request(self, mock_logger: MagicMock) -> None:
        """Log a warning even when the request is None."""
        on_user_login_failed(
            sender=CustomUser,
            credentials={"username": "test"},
            request=None,
        )
        mock_logger.warning.assert_called_once()
