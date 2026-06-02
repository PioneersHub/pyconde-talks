"""
Test helpers for detecting N+1 query patterns.

The standard ``django.test.TestCase.assertNumQueries`` checks an exact total but
needs the caller to know the right number, which becomes brittle as views grow.
``assert_no_n_plus_one`` instead groups captured queries by their normalized
SQL template and fails if any template runs more than a small threshold. That
catches "loop calls .filter() per row" patterns regardless of total query
count, and keeps tests resilient to unrelated query additions.

Usage::

    from utils.test_perf import assert_no_n_plus_one

    def test_dashboard_no_n_plus_one(client, user):
        client.force_login(user)
        with assert_no_n_plus_one():
            client.get(reverse("dashboard_stats"))
"""

import re
from collections import defaultdict
from contextlib import contextmanager
from typing import TYPE_CHECKING

from django.db import connection
from django.test.utils import CaptureQueriesContext


if TYPE_CHECKING:
    from collections.abc import Iterator


# Default maximum repetitions of the same normalized SQL before a test is flagged.
# Two repetitions is the noise floor for prefetch + main query patterns.
DEFAULT_MAX_REPEATS = 2
# Maximum SQL snippet length included in the assertion message so long queries
# don't drown the output; the full statement is still in ``ctx.captured_queries``.
_PREVIEW_LIMIT = 160

# Patterns that collapse parameterized values to a placeholder so SELECT WHERE id=1
# and SELECT WHERE id=2 are treated as the same template.
_RE_INT = re.compile(r"\b\d+\b")
_RE_STRING = re.compile(r"'[^']*'")
_RE_IN_LIST = re.compile(r"\bIN\s*\(([^)]*)\)", re.IGNORECASE)


def _fingerprint(sql: str) -> str:
    """Collapse SQL parameters so structurally identical queries hash to one template."""
    sql = _RE_IN_LIST.sub("IN (?)", sql)
    sql = _RE_STRING.sub("?", sql)
    sql = _RE_INT.sub("?", sql)
    return sql.strip()


@contextmanager
def assert_no_n_plus_one(
    *,
    max_repeats: int = DEFAULT_MAX_REPEATS,
    exempt: tuple[str, ...] = (),
) -> Iterator[CaptureQueriesContext]:
    """
    Fail if any normalized query template runs more than ``max_repeats`` times.

    ``exempt`` is a tuple of case-insensitive substrings; any template containing one
    is allowed unbounded repetition (useful for transactional ``SAVEPOINT`` noise or
    table-creation queries from the test runner).

    Yields the underlying ``CaptureQueriesContext`` for tests that want to inspect
    the raw query list as well.
    """
    ctx = CaptureQueriesContext(connection)
    with ctx:
        yield ctx

    counts: defaultdict[str, int] = defaultdict(int)
    for q in ctx.captured_queries:
        sql = q["sql"]
        if any(sub.lower() in sql.lower() for sub in exempt):
            continue
        counts[_fingerprint(sql)] += 1

    repeated = {sql: n for sql, n in counts.items() if n > max_repeats}
    if repeated:
        lines = ["N+1 query pattern detected:"]
        for sql, n in sorted(repeated.items(), key=lambda kv: -kv[1]):
            preview = sql[:_PREVIEW_LIMIT] + ("..." if len(sql) > _PREVIEW_LIMIT else "")
            lines.append(f"  {n}x  {preview}")
        msg = "\n".join(lines)
        raise AssertionError(msg)
