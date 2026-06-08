"""
Pretalx API client and submission fetching.

All network interaction with the Pretalx REST API is centralized here so that throttling, retry
policy, and the optional dev cache are applied consistently. The :class:`PretalxClient` and
:func:`throttle` helpers are deliberately minimal: they cover only what the importer needs. The
typed response models live in :mod:`~talks.management.commands._pretalx.pretalx_models`.
"""

import functools
import logging
import pickle  # nosec: B403  # dev-only cache of our own API responses
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx2
from django.conf import settings
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from talks.management.commands._pretalx.pretalx_models import Event, Submission
from talks.management.commands._pretalx.types import VerbosityLevel


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from talks.management.commands._pretalx.context import ImportContext

logger = logging.getLogger(__name__)

#: A parsed JSON object as returned by the Pretalx API.
type JSONDict = dict[str, Any]

#: ``expand`` value sent on the submissions endpoint. Since API v1 these sub-documents are no
#: longer inlined by default, so we ask for exactly the ones the importer reads (room, speakers,
#: track, submission type) and nothing more, keeping the payload small.
SUBMISSION_EXPAND = "slots,slots.room,speakers,submission_type,track"

#: Filename of the dev-only fetch cache (written under ``settings.BASE_DIR``).
CACHE_FILENAME = ".pretalx_cache"


def throttle[**P, R](calls: int, seconds: int = 1) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Space calls at least ``seconds / calls`` apart (a simple rate-limiting decorator).

    Pretalx rate-limits aggressively, so the client paces its requests rather than risk a 429.
    Rather than track a window of timestamps, we keep a single "earliest next call is allowed at"
    marker on a monotonic clock and sleep until it passes. Thread-safe via a lock.
    """
    min_interval = seconds / calls

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        lock = threading.Lock()
        next_allowed = 0.0  # monotonic timestamp; 0.0 lets the first call run immediately

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            nonlocal next_allowed
            with lock:
                now = time.monotonic()
                if now < next_allowed:
                    time.sleep(next_allowed - now)
                    now = next_allowed
                next_allowed = now + min_interval
                return func(*args, **kwargs)

        return wrapper

    return decorator


class PretalxClient:
    """
    Minimal synchronous client for the Pretalx REST API.

    Only the two calls the importer needs are implemented: :meth:`submissions` (a paginated list)
    and :meth:`event` (a single object). Requests carry the API token and version headers and are
    throttled. Submissions that fail validation are skipped with a warning rather than aborting the
    whole fetch, so one malformed record cannot block an import.
    """

    def __init__(
        self,
        api_token: str,
        api_base_url: str = "https://pretalx.com/",
        timeout: int | None = None,
        *,
        api_version: str = "v1",
        calls_per_second: int = 2,
    ) -> None:
        self._api_token = api_token
        self._api_base_url = api_base_url if api_base_url.endswith("/") else api_base_url + "/"
        self._api_version = api_version
        self._timeout = timeout or 60.0
        # ``set_throttling`` is what actually binds ``_get_throttled``; declare the type here.
        self._get_throttled: Callable[..., httpx2.Response]
        self.set_throttling(calls=calls_per_second, seconds=1)

    def set_throttling(self, calls: int, seconds: int) -> None:
        """Limit outgoing requests to *calls* per *seconds*."""
        self._get_throttled = throttle(calls, seconds)(self._get)

    def _get(
        self,
        endpoint: str,
        params: httpx2.QueryParams | dict[str, Any] | None = None,
    ) -> httpx2.Response:
        """Issue a single GET against *endpoint* (a path, joined onto the configured base URL)."""
        headers = {"Pretalx-Version": self._api_version}
        if self._api_token:
            headers["Authorization"] = f"Token {self._api_token}"

        url = httpx2.URL(self._api_base_url).join(endpoint).copy_merge_params(params or {})
        logger.debug("GET %s", url)
        return httpx2.get(url, timeout=self._timeout, headers=headers, follow_redirects=True)

    def _get_json(
        self,
        endpoint: str,
        params: httpx2.QueryParams | dict[str, Any] | None = None,
    ) -> JSONDict:
        """GET *endpoint* through the throttle and return the parsed JSON body."""
        resp = self._get_throttled(endpoint, params)
        resp.raise_for_status()
        return cast("JSONDict", resp.json())

    def _paginate(self, first_page: JSONDict) -> Iterator[JSONDict]:
        """Yield every result dict across all pages, following ``next`` links."""
        page = first_page
        yield from page["results"]
        while (next_url := page.get("next")) is not None:
            url = httpx2.URL(next_url)
            page = self._get_json(url.path, url.params)
            yield from page["results"]

    def submissions(self, event_slug: str) -> list[Submission]:
        """List all submissions for *event_slug*, fully paginated and validated."""
        endpoint = f"/api/events/{event_slug}/submissions/"
        first_page = self._get_json(endpoint, {"expand": SUBMISSION_EXPAND})
        return list(self._validate_each(self._paginate(first_page)))

    def event(self, event_slug: str) -> Event | None:
        """Return the :class:`Event` for *event_slug*, or ``None`` if it fails to validate."""
        raw = self._get_json(f"/api/events/{event_slug}/")
        try:
            return Event.model_validate(raw)
        except ValidationError as exc:
            logger.warning("Could not parse event %s: %s", event_slug, exc)
            return None

    @staticmethod
    def _validate_each(raw_items: Iterator[JSONDict]) -> Iterator[Submission]:
        """Validate each raw submission, skipping (and logging) any that do not parse."""
        for raw in raw_items:
            try:
                yield Submission.model_validate(raw)
            except ValidationError as exc:
                # ``raw`` is typed as a dict, but a malformed page could place a non-dict in
                # ``results``; guard so reading its code cannot raise inside the error handler.
                code = raw.get("code", "?") if isinstance(raw, dict) else "?"
                logger.warning("Skipping unparsable submission %s: %s", code, exc)


def fetch_submissions(
    client: PretalxClient,
    event_slug: str,
    ctx: ImportContext,
) -> list[Submission]:
    """
    Return all submissions for *event_slug*, using the dev cache when enabled.

    A cache hit short-circuits before any network call; only the live fetch is wrapped in the
    exponential-backoff retry (transient transport/HTTP-status errors only). Per-submission schema
    failures are swallowed inside :meth:`PretalxClient.submissions`, so they are never retried.
    """
    cache_file = _cache_path(event_slug) if ctx.use_cache else None
    if cache_file is not None:
        cached = _read_cache(cache_file)
        if cached is not None:
            return cached

    submissions = _fetch_with_retry(client, event_slug, ctx)

    if cache_file is not None:
        _write_cache(cache_file, submissions, ctx)
    return submissions


def _fetch_with_retry(
    client: PretalxClient,
    event_slug: str,
    ctx: ImportContext,
) -> list[Submission]:
    """Fetch with exponential-backoff retry; the original error is re-raised once exhausted."""

    @retry(
        stop=stop_after_attempt(ctx.max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((httpx2.HTTPStatusError, httpx2.RequestError)),
        reraise=True,
    )
    def _fetch() -> list[Submission]:
        return client.submissions(event_slug)

    return _fetch()


def _cache_path(event_slug: str) -> Path:
    """Return the dev-cache file path for *event_slug* under the project base directory."""
    return Path(settings.BASE_DIR) / f"{CACHE_FILENAME}_{event_slug}"


def _read_cache(path: Path) -> list[Submission] | None:
    """
    Load cached submissions, or ``None`` when the cache is missing, unreadable, or stale.

    The cache is best-effort: a truncated/empty file (an interrupted ``pickle.dump``) or a payload
    from an older format is treated as a miss so the caller just fetches fresh.
    """
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            data = pickle.load(f)  # noqa: S301  # nosec: B301
    except pickle.PickleError, OSError, EOFError, AttributeError, ValueError:
        return None
    return cast("list[Submission]", data) if isinstance(data, list) else None


def _write_cache(path: Path, submissions: list[Submission], ctx: ImportContext) -> None:
    """Persist *submissions* to the dev cache, warning (not raising) on I/O errors."""
    try:
        with path.open("wb") as f:
            pickle.dump(submissions, f)
    except OSError:
        ctx.log(
            f"Failed to cache Pretalx talks to {path}",
            VerbosityLevel.NORMAL,
            "WARNING",
        )
