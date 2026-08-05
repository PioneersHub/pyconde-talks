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
"""

import time
from dataclasses import dataclass
from typing import Final

from django.conf import settings
from django.core.cache import cache


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
    """Return whether *identity* has already used up its allowance for *scope*."""
    return int(cache.get(_bucket_key(scope, identity, rule.window_seconds), 0)) >= rule.limit


def consume(scope: str, identity: str, rule: RateLimit) -> int:
    """Record one action against the allowance and return the new count."""
    key = _bucket_key(scope, identity, rule.window_seconds)
    # ``add`` then ``incr``: ``add`` is a no-op when the key exists, and ``incr`` is atomic on
    # Redis and memcached. A lost race on local memory costs one extra permitted question.
    cache.add(key, 0, timeout=rule.window_seconds)
    try:
        return int(cache.incr(key))
    except ValueError:
        # The key expired between the add and the incr. Django raises rather than treating a
        # missing key as zero, so re-seed instead of letting a boundary crossing 500.
        cache.set(key, 1, timeout=rule.window_seconds)
        return 1


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
