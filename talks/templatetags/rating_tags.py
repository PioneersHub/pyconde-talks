"""Template tags for rendering talk ratings."""

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

from .svg_tags import STAR_PATH


register = template.Library()

# Constants
HALF_STAR_THRESHOLD = 0.5


@register.simple_tag
def star_rating(average_rating: float | None, rating_count: int = 0) -> SafeString:
    """
    Render a star rating display.

    Args:
        average_rating: The average rating (1-5 scale), or None if no ratings
        rating_count: The number of ratings

    Returns:
        HTML string with star rating display

    """
    if average_rating is None or rating_count == 0:
        return format_html(
            "{}",
            SafeString(  # nosec: B703 - Static HTML string, no user input
                '<span class="text-sm text-subtle">No ratings yet</span>',
            ),
        )

    # Round to nearest 0.5
    rounded_rating = round(average_rating * 2) / 2

    # Generate star icons
    full_stars = int(rounded_rating)
    half_star = rounded_rating - full_stars >= HALF_STAR_THRESHOLD
    empty_stars = 5 - full_stars - (1 if half_star else 0)

    # Build star HTML parts
    stars_parts = []

    # Full stars
    full_star_svg = (
        '<svg class="h-4 w-4 text-yellow-400 fill-current" '
        f'viewBox="0 0 20 20"><path d="{STAR_PATH}"/></svg>'
    )
    stars_parts.extend([full_star_svg] * full_stars)

    # Half star
    if half_star:
        half_star_svg = (
            '<svg class="h-4 w-4 text-yellow-400" viewBox="0 0 20 20">'
            '<defs><linearGradient id="half">'
            '<stop offset="50%" stop-color="currentColor"/>'
            '<stop offset="50%" stop-color="rgb(209 213 219)" stop-opacity="1"/>'
            "</linearGradient></defs>"
            f'<path fill="url(#half)" d="{STAR_PATH}"/></svg>'
        )
        stars_parts.append(half_star_svg)

    # Empty stars
    empty_star_svg = (
        '<svg class="h-4 w-4 text-gray-300 fill-current" '
        f'viewBox="0 0 20 20"><path d="{STAR_PATH}"/></svg>'
    )
    stars_parts.extend([empty_star_svg] * empty_stars)

    # Combine all parts using format_html to ensure proper escaping of the rating values
    # The SVG HTML is safe as it contains only constants, no user input
    stars_html = "".join(stars_parts)

    # Use format_html which automatically escapes the numeric values
    # SafeString is used only for the static SVG content (no user input)
    # Format the rating value first since format_html doesn't support format specs
    formatted_rating = f"{average_rating:.1f}"

    return format_html(
        '<div class="flex items-center gap-1">{}<span class="text-sm text-muted ml-1">'
        "{} ({})</span></div>",
        SafeString(stars_html),  # nosec: B703 - SVGs contain only static constants
        formatted_rating,  # Pre-formatted, will be escaped
        rating_count,  # Will be properly escaped
    )
