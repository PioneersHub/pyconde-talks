"""Tests for the N+1 detection helper in ``utils.test_perf``."""

from datetime import timedelta

import pytest
from django.utils import timezone
from model_bakery import baker

from talks.models import Room, Streaming, Talk
from utils.test_perf import _fingerprint, assert_no_n_plus_one


class TestFingerprint:
    """The fingerprint normalizes parameterized SQL so equivalent queries hash equally."""

    def test_collapses_integer_parameters(self) -> None:
        """Different literal ints in the same statement collapse to one fingerprint."""
        assert _fingerprint("SELECT * FROM t WHERE id = 1") == _fingerprint(
            "SELECT * FROM t WHERE id = 42",
        )

    def test_collapses_string_parameters(self) -> None:
        """Different literal strings collapse to one fingerprint."""
        assert _fingerprint("SELECT * FROM t WHERE x = 'a'") == _fingerprint(
            "SELECT * FROM t WHERE x = 'b'",
        )

    def test_collapses_in_lists_of_different_sizes(self) -> None:
        """``IN (...)`` lists of different lengths collapse to the same fingerprint."""
        assert _fingerprint("SELECT * FROM t WHERE id IN (1, 2)") == _fingerprint(
            "SELECT * FROM t WHERE id IN (3, 4, 5, 6)",
        )


@pytest.mark.django_db
class TestAssertNoNPlusOne:
    """The context manager fires when a query template repeats too many times."""

    def test_passes_when_each_template_runs_once(self) -> None:
        """A handful of distinct queries do not trip the detector."""
        with assert_no_n_plus_one():
            list(Talk.objects.all())
            list(Room.objects.all())

    def test_detects_n_plus_one_pattern(self) -> None:
        """A loop of single-row lookups raises AssertionError listing the offending SQL."""
        room = baker.make(Room)
        now = timezone.now()
        # Five Streamings; querying each by id individually is the classic N+1.
        streamings = [
            baker.make(
                Streaming,
                room=room,
                start_time=now + timedelta(hours=i),
                end_time=now + timedelta(hours=i + 1),
            )
            for i in range(5)
        ]

        def trigger() -> None:
            with assert_no_n_plus_one(max_repeats=2):
                for s in streamings:
                    Streaming.objects.get(pk=s.pk)

        with pytest.raises(AssertionError, match="N\\+1 query pattern detected"):
            trigger()

    def test_exempt_substring_silences_noise(self) -> None:
        """Templates matching ``exempt`` substrings are ignored, even if repeated."""
        room = baker.make(Room)
        now = timezone.now()
        for i in range(5):
            baker.make(
                Streaming,
                room=room,
                start_time=now + timedelta(hours=i),
                end_time=now + timedelta(hours=i + 1),
            )

        # Without exempt, this would fail; with exempt, all SELECTs on streaming are ignored.
        with assert_no_n_plus_one(max_repeats=2, exempt=('FROM "talks_streaming"',)):
            for s in Streaming.objects.all():
                Streaming.objects.get(pk=s.pk)
