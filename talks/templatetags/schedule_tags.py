"""Template tags for the schedule grid view."""

from typing import TYPE_CHECKING, Any

from django import template


if TYPE_CHECKING:
    from datetime import datetime


register = template.Library()


@register.simple_tag
def schedule_cell(
    grid: dict[datetime, dict[int, Any]],
    time_slot: datetime,
    room_id: int,
) -> Any | None:
    """
    Look up a talk in the schedule grid by time slot and room ID.

    Usage::

        {% load schedule_tags %}
        {% schedule_cell grid slot room.id as talk %}
    """
    row = grid.get(time_slot)
    if row is None:
        return None
    return row.get(room_id)
