"""Template tags for rendering talk star ratings."""

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from .svg_tags import svg


register = template.Library()

# Constants
HALF_STAR_THRESHOLD = 0.5
MAX_STARS = 5


def _star_svg(css_class: str) -> str:
    """Return the star SVG loaded from the svg/ folder with the given CSS classes."""
    return str(svg("star", css_class))


def _half_star_html() -> str:
    """Return HTML for a half-filled star using two overlapping SVGs."""
    # Container is w-4 h-4 with relative positioning.
    # Background: empty gray star. Foreground: yellow star clipped to 50% width.
    empty = _star_svg("absolute inset-0 h-4 w-4 text-gray-300 fill-current")
    full = _star_svg("h-4 w-4 text-yellow-400 fill-current")
    return (
        '<span class="relative inline-block h-4 w-4">'
        f"{empty}"
        f'<span class="absolute inset-0 overflow-hidden" style="width:50%">{full}</span>'
        "</span>"
    )


@register.simple_tag
def star_rating(average_rating: float | None, rating_count: int = 0) -> SafeString:
    """
    Render a star rating display with filled, half, and empty stars.

    Args:
        average_rating: The average rating (1-5 scale), or None if no ratings.
        rating_count: The number of ratings.

    Returns:
        HTML string with star rating display.

    """
    if average_rating is None or rating_count == 0:
        return SafeString(  # nosec: B703 - Static HTML, no user input
            '<span class="text-sm text-subtle">No ratings yet</span>',
        )

    # Round to nearest 0.5
    rounded_rating = round(average_rating * 2) / 2

    full_stars = int(rounded_rating)
    has_half_star = (rounded_rating - full_stars) >= HALF_STAR_THRESHOLD
    empty_stars = MAX_STARS - full_stars - (1 if has_half_star else 0)

    parts: list[str] = []

    # Full stars
    parts.extend([_star_svg("h-4 w-4 text-yellow-400 fill-current")] * full_stars)

    # Half star
    if has_half_star:
        parts.append(_half_star_html())

    # Empty stars
    parts.extend([_star_svg("h-4 w-4 text-gray-300 fill-current")] * empty_stars)

    stars_html = "".join(parts)
    formatted_rating = f"{average_rating:.1f}"

    # SafeString for SVG content is safe: it's loaded from our own SVG files
    return format_html(
        '<div class="flex items-center gap-1">'
        "{}"
        '<span class="text-sm text-muted ml-1">{} ({})</span>'
        "</div>",
        SafeString(stars_html),  # nosec: B703 - SVGs loaded from static files
        formatted_rating,
        rating_count,
    )
