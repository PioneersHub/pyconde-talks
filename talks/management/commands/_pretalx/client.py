"""
Pretalx API client setup and data fetching with retry logic.

All network interaction with the Pretalx REST API is centralized here so that retry policy and
caching are applied consistently.
"""

from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx
from django.conf import settings
from pydantic import ValidationError
from pytanis import PretalxClient
from pytanis.config import PretalxCfg
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from talks.management.commands._pretalx.types import PytanisCfg, VerbosityLevel


if TYPE_CHECKING:
    from pytanis.pretalx.models import Submission

    from talks.management.commands._pretalx.context import ImportContext


def setup_pretalx_client(
    api_token: str,
    api_base_url: str = "https://pretalx.com/",
    timeout: int | None = None,
    calls_per_second: int = 2,
) -> PretalxClient:
    """Build and configure a :class:`~pytanis.PretalxClient` with throttling."""
    pretalx_cfg = PretalxCfg(api_token=api_token, api_base_url=api_base_url, timeout=timeout)
    config = PytanisCfg(Pretalx=pretalx_cfg)
    client = PretalxClient(config)  # type: ignore[arg-type]
    client.set_throttling(calls=calls_per_second, seconds=1)
    return client


def fetch_talks_with_retry(
    pretalx: PretalxClient,
    pretalx_event_slug: str,
    ctx: ImportContext,
) -> tuple[int, list[Submission]]:
    """
    Fetch submissions with exponential-backoff retry and optional pickle cache.

    The retry wraps ``httpx`` transport errors, ``RuntimeError``, and Pydantic ``ValidationError``
    up to ``ctx.max_retries`` attempts.
    """

    @retry(
        stop=stop_after_attempt(ctx.max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type(
            (httpx.HTTPStatusError, httpx.RequestError, RuntimeError, ValidationError),
        ),
    )
    def _do_fetch() -> tuple[int, list[Submission]]:
        if not settings.PICKLE_PRETALX_TALKS:
            count, submissions = pretalx.submissions(pretalx_event_slug)
            return (count, list(submissions))
        return _fetch_with_pickle_cache(pretalx, pretalx_event_slug, ctx)

    return _do_fetch()


def _fetch_with_pickle_cache(
    pretalx: PretalxClient,
    pretalx_event_slug: str,
    ctx: ImportContext,
) -> tuple[int, list[Submission]]:
    """
    Read from ``.pretalx_cache`` pickle if present; otherwise fetch and persist.

    Intended **only** for local development to avoid repeated API calls.
    """
    import pickle  # nosec: B403  # noqa: PLC0415

    pickle_file = Path(".pretalx_cache")

    if pickle_file.exists():
        try:
            with pickle_file.open("rb") as f:
                return cast(
                    "tuple[int, list[Submission]]",
                    pickle.load(f),  # noqa: S301  # nosec: B301
                )
        except (pickle.PickleError, OSError):  # fmt: skip
            pass

    count, submissions = pretalx.submissions(pretalx_event_slug)
    result = (count, list(submissions))
    try:
        with pickle_file.open("wb") as wb_file:
            pickle.dump(result, wb_file)
    except OSError:
        ctx.log(
            f"Failed to cache Pretalx talks to {pickle_file}",
            VerbosityLevel.NORMAL,
            "WARNING",
        )
    return result
