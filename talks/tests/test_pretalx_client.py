"""
Unit tests for the in-repo Pretalx HTTP client.

These mock the network with ``httpx2_mock`` (a respx router on the httpx2 backend) so the client's
real behaviour is exercised: pagination, validation-skip, header construction, the ``expand`` query,
throttling, retry/reraise, and the optional dev cache. respx bridges httpx -> httpx2 internally, so
canned responses are built with ``httpx.Response`` while raised transport errors use ``httpx2``.
"""
# ruff: noqa: PLR2004

import time
from typing import TYPE_CHECKING, Any

import httpx
import httpx2
import pytest

from talks.management.commands._pretalx import client as client_mod
from talks.management.commands._pretalx.client import (
    SUBMISSION_EXPAND,
    PretalxClient,
    fetch_submissions,
    throttle,
)
from talks.management.commands._pretalx.context import ImportContext
from talks.management.commands._pretalx.types import VerbosityLevel


if TYPE_CHECKING:
    from pathlib import Path

    import respx


_HOST = "pretalx.com"
_SUBS_PATH = "/api/events/evt/submissions/"
_EVENT_PATH = "/api/events/evt/"


def _noop_log(*_args: Any, **_kwargs: Any) -> None:
    """Silent log callback for the import context."""


def _ctx(**overrides: Any) -> ImportContext:
    return ImportContext(verbosity=VerbosityLevel.MINIMAL, log_fn=_noop_log, **overrides)


def _sub(code: str, *, title: str = "T", state: str = "confirmed") -> dict[str, Any]:
    return {"code": code, "title": title, "state": state, "speakers": []}


def _page(results: list[dict[str, Any]], next_url: str | None = None) -> dict[str, Any]:
    return {"count": len(results), "next": next_url, "results": results}


def _client() -> PretalxClient:
    return PretalxClient(api_token="tok", api_base_url="https://pretalx.com/")


def test_submissions_follows_pagination(httpx2_mock: respx.Router) -> None:
    """submissions() walks every page via the ``next`` link and returns validated models."""
    page2 = "https://pretalx.com/api/events/evt/submissions/?expand=x&page=2"
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).mock(
        side_effect=[
            httpx.Response(200, json=_page([_sub("A")], next_url=page2)),
            httpx.Response(200, json=_page([_sub("B")])),
        ],
    )

    submissions = _client().submissions("evt")

    assert [s.code for s in submissions] == ["A", "B"]


def test_submissions_skips_unparsable_records(httpx2_mock: respx.Router) -> None:
    """A record that fails validation is skipped; the rest are kept."""
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(
        json=_page([_sub("GOOD"), {"code": "BAD"}]),  # BAD lacks title/state
    )

    submissions = _client().submissions("evt")

    assert [s.code for s in submissions] == ["GOOD"]


def test_submissions_sends_expand_query(httpx2_mock: respx.Router) -> None:
    """The submissions request asks for exactly the importer's expansions."""
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([]))

    _client().submissions("evt")

    assert httpx2_mock.calls.last.request.url.params["expand"] == SUBMISSION_EXPAND


def test_request_carries_auth_and_version_headers(httpx2_mock: respx.Router) -> None:
    """A token client sends the version header and a ``Token`` Authorization header."""
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([]))

    _client().submissions("evt")

    request = httpx2_mock.calls.last.request
    assert request.headers["Pretalx-Version"] == "v1"
    assert request.headers["Authorization"] == "Token tok"


def test_request_omits_auth_without_token(httpx2_mock: respx.Router) -> None:
    """A tokenless client sends no Authorization header."""
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([]))

    PretalxClient(api_token="", api_base_url="https://pretalx.com/").submissions("evt")

    assert "Authorization" not in httpx2_mock.calls.last.request.headers


def test_event_parses_payload(httpx2_mock: respx.Router) -> None:
    """event() returns a parsed Event for a well-formed payload."""
    httpx2_mock.get(host=_HOST, path=_EVENT_PATH).respond(
        json={"name": {"en": "PyCon"}, "slug": "evt"},
    )

    event = _client().event("evt")

    assert event is not None
    assert event.name.en == "PyCon"


def test_event_returns_none_on_bad_payload(httpx2_mock: respx.Router) -> None:
    """event() returns None when the payload fails validation (rather than raising)."""
    httpx2_mock.get(host=_HOST, path=_EVENT_PATH).respond(json={"slug": "evt"})  # missing name

    assert _client().event("evt") is None


def test_throttle_spaces_successive_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second call within the window sleeps for the remaining interval."""
    sleeps: list[float] = []
    clock = iter([100.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", sleeps.append)

    wrapped = throttle(calls=2, seconds=1)(lambda: None)  # min_interval = 0.5s
    wrapped()
    wrapped()

    assert sleeps == [pytest.approx(0.5)]


def test_submissions_skips_records_with_unexpanded_track(httpx2_mock: respx.Router) -> None:
    """A record whose track is a bare id (not the expanded object) is skipped, not page-fatal."""
    bad: dict[str, Any] = {
        "code": "BAD001",
        "title": "T",
        "state": "confirmed",
        "speakers": [],
        "track": 7,  # bare id, not the expanded object
    }
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([_sub("GOOD"), bad]))

    submissions = _client().submissions("evt")

    assert [s.code for s in submissions] == ["GOOD"]


def test_fetch_submissions_retries_then_succeeds(httpx2_mock: respx.Router) -> None:
    """A transient transport error is retried, and the later success is returned."""
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).mock(
        side_effect=[
            httpx2.ConnectError("boom"),
            httpx.Response(200, json=_page([_sub("A")])),
        ],
    )

    submissions = fetch_submissions(_client(), "evt", _ctx(use_cache=False, max_retries=3))

    assert [s.code for s in submissions] == ["A"]


def test_fetch_submissions_retries_on_http_status_error(httpx2_mock: respx.Router) -> None:
    """A 5xx status is retried (HTTPStatusError leg) and the retry's success is returned."""
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=_page([_sub("A")])),
        ],
    )

    submissions = fetch_submissions(_client(), "evt", _ctx(use_cache=False, max_retries=3))

    assert [s.code for s in submissions] == ["A"]
    assert len(httpx2_mock.calls) == 2


def test_fetch_submissions_reraises_after_exhaustion(httpx2_mock: respx.Router) -> None:
    """When retries are exhausted the original transport error propagates (reraise=True)."""
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).mock(side_effect=httpx2.ConnectError("boom"))

    with pytest.raises(httpx2.ConnectError):
        fetch_submissions(_client(), "evt", _ctx(use_cache=False, max_retries=2))


def _use_tmp_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the dev cache at *tmp_path*, keyed per event slug like the real path."""
    monkeypatch.setattr(client_mod, "_cache_path", lambda slug: tmp_path / f".pretalx_cache_{slug}")


def test_fetch_submissions_uses_cache(
    httpx2_mock: respx.Router,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the cache on, a second fetch is served from disk without another request."""
    _use_tmp_cache(monkeypatch, tmp_path)
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([_sub("A")]))
    ctx = _ctx(use_cache=True, max_retries=3)
    client = _client()

    first = fetch_submissions(client, "evt", ctx)
    second = fetch_submissions(client, "evt", ctx)

    assert [s.code for s in first] == ["A"]
    assert [s.code for s in second] == ["A"]
    assert len(httpx2_mock.calls) == 1  # second call hit the cache, not the network


def test_fetch_submissions_cache_is_keyed_by_event(
    httpx2_mock: respx.Router,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached event does not leak into a different event's fetch."""
    _use_tmp_cache(monkeypatch, tmp_path)
    httpx2_mock.get(host=_HOST, path="/api/events/evt1/submissions/").respond(
        json=_page([_sub("A")])
    )
    httpx2_mock.get(host=_HOST, path="/api/events/evt2/submissions/").respond(
        json=_page([_sub("B")])
    )
    ctx = _ctx(use_cache=True, max_retries=3)
    client = _client()

    first = fetch_submissions(client, "evt1", ctx)
    second = fetch_submissions(client, "evt2", ctx)

    assert [s.code for s in first] == ["A"]
    assert [s.code for s in second] == ["B"]  # evt2 is not served evt1's cache


def test_fetch_submissions_recovers_from_corrupt_cache(
    httpx2_mock: respx.Router,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt cache file is ignored and the data is fetched fresh."""
    _use_tmp_cache(monkeypatch, tmp_path)
    (tmp_path / ".pretalx_cache_evt").write_bytes(b"not a pickle")
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([_sub("A")]))

    submissions = fetch_submissions(_client(), "evt", _ctx(use_cache=True, max_retries=3))

    assert [s.code for s in submissions] == ["A"]


def test_fetch_submissions_recovers_from_empty_cache(
    httpx2_mock: respx.Router,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty/truncated cache (interrupted dump) is treated as a miss, not a crash."""
    _use_tmp_cache(monkeypatch, tmp_path)
    (tmp_path / ".pretalx_cache_evt").write_bytes(b"")  # EOFError on pickle.load
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([_sub("A")]))

    submissions = fetch_submissions(_client(), "evt", _ctx(use_cache=True, max_retries=3))

    assert [s.code for s in submissions] == ["A"]


def test_fetch_submissions_ignores_stale_format_cache(
    httpx2_mock: respx.Router,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache from an older format (a non-list payload) is discarded and refetched."""
    import pickle  # noqa: PLC0415  # nosec: B403

    _use_tmp_cache(monkeypatch, tmp_path)
    (tmp_path / ".pretalx_cache_evt").write_bytes(
        pickle.dumps((1, ["stale"]))
    )  # old (count, list) tuple
    httpx2_mock.get(host=_HOST, path=_SUBS_PATH).respond(json=_page([_sub("A")]))

    submissions = fetch_submissions(_client(), "evt", _ctx(use_cache=True, max_retries=3))

    assert [s.code for s in submissions] == ["A"]
