"""Time formatting filters for Django templates."""

from django import template


register = template.Library()


@register.filter()
def format_seconds(seconds: float | str) -> str:
    """
    Convert seconds into a human-readable time string (H:MM:SS or MM:SS).

    Args:
        seconds: The number of seconds to format. Can be negative.

    Returns:
        A formatted time string

    Examples:
        >>> format_seconds(103)
        '1:43'
        >>> format_seconds(3700)
        '1:01:40'
        >>> format_seconds("3600")
        '1:00:00'
        >>> format_seconds(-75)
        '-1:15'
        >>> format_seconds(0)
        '0:00'
        >>> format_seconds("invalid")
        '0:00'

    """
    try:
        seconds_int = int(float(seconds))
    except (ValueError, TypeError):
        return "0:00"

    # Handle negative values
    is_negative = seconds_int < 0
    seconds_int = abs(seconds_int)

    hours, remainder = divmod(seconds_int, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        formatted_time = f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        formatted_time = f"{minutes}:{seconds:02d}"

    if is_negative:
        formatted_time = f"-{formatted_time}"

    return formatted_time
