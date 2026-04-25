"""
RoomAvailability and SpecialSlot: the scheduling bookkeeping used by generate_fake_talks.

Extracted from generate_fake_talks.py so the command file stays focused on the Command class.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from talks.models import Room, Talk

from .constants import CONFERENCE_DAY_EXTRA_MINUTES, CONFERENCE_DAY_HOURS, SLOT_ALIGNMENT_MINUTES


# ruff: noqa: S311
# This module generates seed data for development and tests; cryptographic randomness is not
# needed. Ruff's S311 is suppressed here, bandit's B311 via the _fake_talks exclude in
# pyproject.toml [tool.bandit].


@dataclass
class SpecialSlot:
    """A time-pinned slot ensuring specific scheduling scenarios (past, live, near-future)."""

    time: datetime
    room: Room | None = None


class RoomAvailability:
    """
    Track free time intervals per room for conflict-free talk placement.

    Each room starts with one free interval per conference day (09:00 - 17:30).
    ``find_slot`` picks a random available start time aligned to 30-min
    boundaries, and ``reserve`` splits the containing interval so the time
    cannot be reused.
    """

    def __init__(
        self,
        rooms: dict[str, list[Room]],
        base_time: datetime,
        days: int,
    ) -> None:
        """
        Initialize with full-day free intervals for every room.

        Pre-existing talks in the database are automatically reserved so
        that new talks never overlap with them.
        """
        self._free: dict[int, list[tuple[datetime, datetime]]] = {}
        all_rooms: list[Room] = []
        for room_list in rooms.values():
            for room in room_list:
                intervals: list[tuple[datetime, datetime]] = []
                for day in range(days):
                    day_start = base_time + timedelta(days=day)
                    day_end = day_start + timedelta(
                        hours=CONFERENCE_DAY_HOURS,
                        minutes=CONFERENCE_DAY_EXTRA_MINUTES,
                    )
                    intervals.append((day_start, day_end))
                self._free[room.pk] = intervals
                all_rooms.append(room)

        # Reserve slots occupied by talks already in the database so that
        # subsequent scheduling never overlaps with pre-existing data.
        if all_rooms:
            self._reserve_existing_talks(all_rooms)

    def _reserve_existing_talks(self, rooms: list[Room]) -> None:
        """Reserve intervals for talks already persisted in the database."""
        existing = Talk.objects.filter(room__in=rooms).values_list(
            "room_id",
            "start_time",
            "duration",
        )
        for room_id, start_time, duration in existing:
            if start_time and duration:
                self.reserve_by_pk(room_id, start_time, duration)

    def reserve_by_pk(self, room_pk: int, start: datetime, duration: timedelta) -> None:
        """Mark ``[start, start + duration)`` as occupied, addressed by room PK."""
        end = start + duration
        new_intervals: list[tuple[datetime, datetime]] = []
        for iv_start, iv_end in self._free.get(room_pk, []):
            if iv_start < end and iv_end > start:
                if iv_start < start:
                    new_intervals.append((iv_start, start))
                if iv_end > end:
                    new_intervals.append((end, iv_end))
            else:
                new_intervals.append((iv_start, iv_end))
        self._free[room_pk] = new_intervals

    @staticmethod
    def _aligned_starts(
        iv_start: datetime,
        iv_end: datetime,
        duration: timedelta,
    ) -> list[datetime]:
        """Return 30-min-aligned start times that fit *duration* inside the interval."""
        remainder = iv_start.minute % SLOT_ALIGNMENT_MINUTES
        if remainder:
            first = iv_start + timedelta(minutes=SLOT_ALIGNMENT_MINUTES - remainder)
            first = first.replace(second=0, microsecond=0)
        else:
            first = iv_start.replace(second=0, microsecond=0)

        results: list[datetime] = []
        latest = iv_end - duration
        current = first
        while current <= latest:
            results.append(current)
            current += timedelta(minutes=SLOT_ALIGNMENT_MINUTES)
        return results

    def is_available(
        self,
        room: Room,
        start: datetime,
        duration: timedelta,
    ) -> bool:
        """Return ``True`` if ``[start, start + duration)`` fits inside a free interval."""
        end = start + duration
        for iv_start, iv_end in self._free.get(room.pk, []):
            if iv_start <= start and iv_end >= end:
                return True
        return False

    def find_slot(
        self,
        rooms: list[Room],
        duration: timedelta,
    ) -> tuple[Room, datetime] | None:
        """Pick a random available ``(room, start_time)`` that fits *duration*, or ``None``."""
        candidates: list[tuple[Room, datetime]] = []
        for room in rooms:
            for iv_start, iv_end in self._free.get(room.pk, []):
                candidates.extend(
                    (room, start) for start in self._aligned_starts(iv_start, iv_end, duration)
                )
        if not candidates:
            return None
        return random.choice(candidates)

    def reserve(self, room: Room, start: datetime, duration: timedelta) -> None:
        """Mark ``[start, start + duration)`` as occupied by splitting free intervals."""
        self.reserve_by_pk(room.pk, start, duration)
