"""Project-wide pytest fixtures."""

from typing import TYPE_CHECKING

import pytest
from django.core.cache import cache
from django.utils import translation


if TYPE_CHECKING:
    from collections.abc import Generator

    from pytest_django import Settings


@pytest.fixture(autouse=True)
def _reset_active_language() -> Generator[None]:
    """
    Start and end every test with no language explicitly activated.

    ``LocaleMiddleware`` activates a language per request but never restores the previous one. On a
    server that is harmless, because the next request activates again. In the test process the
    thread-local survives into the next test, so a single test client call carrying a ``de`` cookie
    leaves German active for whatever runs next. Combined with ``--random-order``, that turns any
    assertion on a translated string into a failure that only shows up under some seeds.
    """
    translation.deactivate()
    yield
    translation.deactivate()


@pytest.fixture(autouse=True)
def _clear_cache(settings: Settings) -> Generator[None]:
    """
    Start and end every test with an empty, in-process cache.

    Anything counter-like (the Q&A rate limiter) or token-like (the allauth OAuth bearer) leaks
    across tests otherwise, because a local-memory cache lives in the test process and is shared by
    the whole run. With ``--random-order`` the resulting failure only appears under some seeds.

    The backend is forced to local memory rather than trusting the configured one. ``cache.clear()``
    on Redis is a ``FLUSHDB``, so a developer or CI job that happens to have ``DJANGO_CACHE_URL``
    pointing at a real Redis would have this fixture wipe that whole database twice per test.
    """
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pytest",
            # Well above the 300-entry default, so a test that writes many keys cannot have its
            # own counters culled out from under it.
            "OPTIONS": {"MAX_ENTRIES": 10_000, "CULL_FREQUENCY": 50},
        },
    }
    cache.clear()
    yield
    cache.clear()
