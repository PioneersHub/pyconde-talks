"""Tests for the speaker-avatar download size cap."""

from typing import TYPE_CHECKING

import pytest

from talks.management.commands._pretalx import avatars


if TYPE_CHECKING:
    import respx


pytestmark = pytest.mark.httpx2(assert_all_called=False)

_URL = "https://cdn.example.com/avatar.png"


def test_download_returns_bytes_within_cap(httpx2_mock: respx.Router) -> None:
    """A normally-sized image is downloaded and returned."""
    httpx2_mock.get(_URL).respond(200, content=b"tiny-image-bytes")
    assert avatars.download_avatar_bytes_sync(_URL) == b"tiny-image-bytes"


def test_download_rejects_oversized_body(
    httpx2_mock: respx.Router,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body larger than the cap is rejected (returns None) instead of being buffered."""
    monkeypatch.setattr(avatars, "_MAX_AVATAR_BYTES", 4)
    httpx2_mock.get(_URL).respond(200, content=b"way-too-large")
    assert avatars.download_avatar_bytes_sync(_URL) is None


def test_download_returns_none_on_http_error(httpx2_mock: respx.Router) -> None:
    """A non-2xx response yields None rather than raising."""
    httpx2_mock.get(_URL).respond(404)
    assert avatars.download_avatar_bytes_sync(_URL) is None
