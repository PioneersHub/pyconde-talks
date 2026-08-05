"""
Cache-backed rate limiting for user-generated Q&A content.

Keyed per user account, never per IP. At the conference venue ~2000 attendees reach the site
through one NAT address, so a per-IP limit there is collective punishment: one spammer, or
simply the rush after a popular talk, would lock out the whole room. This mirrors the reasoning
already recorded for ``ACCOUNT_RATE_LIMITS`` in the settings module.

Q&A always requires a login, so there is always an account to key on and no anonymous case to
invent. Note the limiter is only as global as the cache backend: on the default per-process
local memory cache the allowance is effectively per worker. That is fine for a single-worker
deployment, and ``DJANGO_CACHE_URL`` points at Redis for anything larger.

A cache that cannot be reached fails OPEN: the action is allowed and a warning is logged. Django
does not wrap backend errors, so a Redis blip would otherwise surface as an uncaught driver
exception and turn every question submission into a 500. Losing the limit for the duration of an
outage is a much smaller problem than losing the Q&A, which is the same trade-off
``TURNSTILE_FAIL_OPEN`` makes for the captcha.
"""

import time
from dataclasses import dataclass
from typing import Final

import structlog
from django.conf import settings
from django.core.cache import cache


logger = structlog.get_logger(__name__)

# Why the handlers below catch ``Exception`` rather than something specific: Django's cache API
# does not define its own error type and does not wrap the driver's, so what a failing backend
# raises depends entirely on which backend it is - ``redis.exceptions.RedisError`` here, something
# else under memcached. Naming them would mean importing drivers this module has no other use for,
# and a miss would reintroduce exactly the 500 this exists to prevent.
_UNREACHABLE_CACHE = "cache backend errors are driver-specific; see the module docstring"


# Bumped if the stored value ever changes shape, so old entries cannot be misread.
_KEY_PREFIX: Final = "talks.ratelimit:v1"


@dataclass(frozen=True)
class RateLimit:
    """An allowance of *limit* actions within a rolling window of *window_seconds*."""

    limit: int
    window_seconds: int


def _bucket_key(scope: str, identity: str, window_seconds: int) -> str:
    """
    Return the cache key for the current fixed window.

    A fixed window rather than a true sliding one: it costs a single cache operation and the
    worst case is twice the allowance across a boundary, which for "5 questions per 10 minutes"
    is not a way in for a spammer.
    """
    bucket = int(time.time()) // window_seconds
    return f"{_KEY_PREFIX}:{scope}:{identity}:{bucket}"


def is_rate_limited(scope: str, identity: str, rule: RateLimit) -> bool:
    """
    Return whether *identity* has already used up its allowance for *scope*.

    A read-only peek, so two requests can both pass it before either has counted. Use ``claim``
    for anything that has to hold: this is only for reporting.
    """
    try:
        stored = cache.get(_bucket_key(scope, identity, rule.window_seconds), 0)
    except Exception:  # noqa: BLE001  # see _UNREACHABLE_CACHE
        logger.warning("Rate-limit cache unreachable, reporting not limited", scope=scope)
        return False
    return int(stored) >= rule.limit


def consume(scope: str, identity: str, rule: RateLimit) -> int:
    """
    Record one action against the allowance and return the new count.

    Returns 0 when the cache cannot be reached, which every caller reads as "within the
    allowance". See the module docstring for why that direction is the right one.
    """
    key = _bucket_key(scope, identity, rule.window_seconds)
    try:
        # ``add`` then ``incr``: ``add`` is a no-op when the key exists, and ``incr`` is atomic
        # on Redis and memcached. A lost race on local memory costs one extra permitted question.
        cache.add(key, 0, timeout=rule.window_seconds)
        try:
            return int(cache.incr(key))
        except ValueError:
            # The key expired between the add and the incr. Django raises rather than treating a
            # missing key as zero, so re-seed instead of letting a boundary crossing 500.
            cache.set(key, 1, timeout=rule.window_seconds)
            return 1
    except Exception:  # noqa: BLE001  # see _UNREACHABLE_CACHE
        logger.warning("Rate-limit cache unreachable, allowing the action", scope=scope)
        return 0


def claim(scope: str, identity: str, rule: RateLimit) -> bool:
    """
    Count one action and return whether it was within the allowance.

    Checking and then counting as two steps leaves a window where several concurrent requests
    all read a count below the limit and all proceed, so a burst got through whatever the
    allowance said. Counting first and judging the result closes that: the increment is atomic
    on Redis, so exactly one request can be the one that reaches the limit.

    An over-limit attempt is deliberately still counted. It means a caller who keeps hammering
    holds their own window open, rather than being handed a fresh allowance the moment one
    request is refused.
    """
    return consume(scope, identity, rule) <= rule.limit


def refund(scope: str, identity: str, rule: RateLimit) -> None:
    """
    Give back one claimed action, for a submission that turned out not to count.

    ``claim`` has to run before the content is validated, or the check is not atomic. But a
    question rejected for being too long or failing the captcha was never posted, so it should
    not cost the author part of their allowance either. Hence claim-then-refund.
    """
    key = _bucket_key(scope, identity, rule.window_seconds)
    try:
        cache.decr(key)
    except ValueError:
        # The window rolled over before the refund. There is nothing to give back, and seeding
        # the new window with -1 would hand out a free extra question.
        return
    except Exception:  # noqa: BLE001  # see _UNREACHABLE_CACHE
        # Nothing to do: the claim this would refund did not land either.
        logger.warning("Rate-limit cache unreachable, skipping refund", scope=scope)


def seconds_until_reset(rule: RateLimit) -> int:
    """
    Return roughly how long until the current window rolls over.

    Depends only on the window, since the buckets are fixed rather than per-identity sliding.
    Callers round this to whole minutes: an exact countdown would be false precision and would
    tell a prober exactly when to come back.
    """
    elapsed = int(time.time()) % rule.window_seconds
    return max(rule.window_seconds - elapsed, 1)


def question_limits() -> tuple[RateLimit, RateLimit]:
    """
    Return the (per-talk, overall) allowances for asking questions.

    Read from settings on each call rather than captured at import, so an operator can loosen
    them mid-conference without a redeploy - which is exactly when that is needed.
    """
    return (
        RateLimit(
            limit=settings.QA_QUESTION_RATE_LIMIT_PER_TALK,
            window_seconds=settings.QA_QUESTION_RATE_WINDOW_PER_TALK,
        ),
        RateLimit(
            limit=settings.QA_QUESTION_RATE_LIMIT_OVERALL,
            window_seconds=settings.QA_QUESTION_RATE_WINDOW_OVERALL,
        ),
    )
