"""
Shared CSS Grid helpers for talk schedule and chair-grid views.

Both views lay out talks in a grid where columns are rooms and rows are time
boundaries.  Card height is proportional to talk duration.
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.utils import timezone


if TYPE_CHECKING:
    from .models import Talk


def build_grid_slices(talks: list[Talk]) -> tuple[list[datetime], str]:
    """
    Compute CSS Grid named row lines from all talk start/end time boundaries.

    Returns (sorted_boundaries, css_grid_template_rows).  Each boundary becomes a
    named grid line like ``[t-0930]``.  Heights are proportional to the gap between
    consecutive boundaries (2 px/min, minimum 20 px).
    """
    boundaries: set[datetime] = set()
    for t in talks:
        boundaries.add(t.start_time)
        boundaries.add(t.start_time + t.duration)

    sorted_bounds = sorted(boundaries)
    if len(sorted_bounds) < 2:  # noqa: PLR2004
        return sorted_bounds, ""

    px_per_min = 2
    min_px = 20

    parts: list[str] = []
    for i, bound in enumerate(sorted_bounds):
        local = timezone.localtime(bound)
        name = f"t-{local.strftime('%H%M')}"
        if i < len(sorted_bounds) - 1:
            gap_minutes = (sorted_bounds[i + 1] - bound) / timedelta(minutes=1)
            height = max(int(gap_minutes * px_per_min), min_px)
            parts.append(f"[{name}] minmax({height}px, auto)")
        else:
            parts.append(f"[{name}]")

    return sorted_bounds, " ".join(parts)


def grid_line_name(dt: datetime) -> str:
    """Return the CSS grid line name for a datetime, e.g. ``t-0930``."""
    local = timezone.localtime(dt)
    return f"t-{local.strftime('%H%M')}"


def build_time_labels(sorted_bounds: list[datetime]) -> list[dict[str, str]]:
    """
    Return time-label dicts for the first (time) column of the grid.

    Each dict has ``name`` (the CSS grid line name) and ``display`` (H:MM string).
    The final boundary is skipped because it is a talk end-time with no row below it.
    """
    labels: list[dict[str, str]] = []
    seen: set[str] = set()
    for bound in sorted_bounds[:-1]:
        name = grid_line_name(bound)
        if name not in seen:
            seen.add(name)
            local = timezone.localtime(bound)
            labels.append({"name": name, "display": local.strftime("%H:%M")})
    return labels
