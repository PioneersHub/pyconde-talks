"""Template tags for rendering talk star ratings."""

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from .svg_tags import STAR_PATH


register = template.Library()

# Constants
HALF_STAR_THRESHOLD = 0.5
MAX_STARS = 5


def _build_star_svg(css_class: str, fill: str = "") -> str:
    """Build an SVG star element with the given CSS class and optional fill override."""
    fill_attr = f' fill="{fill}"' if fill else ""
    return f'<svg class="{css_class}" viewBox="0 0 20 20"{fill_attr}><path d="{STAR_PATH}"/></svg>'


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
        return format_html(
            '<span class="text-sm text-subtle">No ratings yet</span>',
        )

    # Round to nearest 0.5
    rounded_rating = round(average_rating * 2) / 2

    full_stars = int(rounded_rating)
    has_half_star = (rounded_rating - full_stars) >= HALF_STAR_THRESHOLD
    empty_stars = MAX_STARS - full_stars - (1 if has_half_star else 0)

    parts: list[str] = []

    # Full stars
    full_svg = _build_star_svg("h-4 w-4 text-yellow-400 fill-current")
    parts.extend([full_svg] * full_stars)

    # Half star (uses a linear gradient to fill half)
    if has_half_star:
        half_svg = (
            '<svg class="h-4 w-4 text-yellow-400" viewBox="0 0 20 20">'
            '<defs><linearGradient id="half-star">'
            '<stop offset="50%" stop-color="currentColor"/>'
            '<stop offset="50%" stop-color="rgb(209 213 219)" stop-opacity="1"/>'
            "</linearGradient></defs>"
            f'<path fill="url(#half-star)" d="{STAR_PATH}"/></svg>'
        )
        parts.append(half_svg)

    # Empty stars
    empty_svg = _build_star_svg("h-4 w-4 text-gray-300 fill-current")
    parts.extend([empty_svg] * empty_stars)

    stars_html = "".join(parts)
    formatted_rating = f"{average_rating:.1f}"

    # SafeString for SVG content is safe: it contains only static constants, no user input
    return format_html(
        '<div class="flex items-center gap-1">'
        "{}"
        '<span class="text-sm text-muted ml-1">{} ({})</span>'
        "</div>",
        SafeString(stars_html),  # nosec: B703 - SVGs built from constants only
        formatted_rating,
        rating_count,
    )
