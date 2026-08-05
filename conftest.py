"""Project-wide pytest fixtures."""

from typing import TYPE_CHECKING

import pytest
from django.core.cache import cache
from django.utils import translation


if TYPE_CHECKING:
    from collections.abc import Generator


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
def _clear_cache() -> Generator[None]:
    """
    Start and end every test with an empty cache.

    The default backend is local memory, which lives in the test process and is therefore shared
    by every test in the run. Anything counter-like (the Q&A rate limiter) or token-like (the
    allauth OAuth bearer) would otherwise leak across tests, and with ``--random-order`` the
    resulting failure only appears under some seeds.
    """
    cache.clear()
    yield
    cache.clear()
