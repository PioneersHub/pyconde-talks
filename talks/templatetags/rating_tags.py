"""Template tags for rendering talk ratings."""

from django import template
from django.utils.safestring import mark_safe


register = template.Library()

# Constants
HALF_STAR_THRESHOLD = 0.5
STAR_PATH = (
    "M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 "
    "0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 "
    "1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-"
    "1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-"
    "1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"
)


@register.simple_tag
def star_rating(average_rating: float | None, rating_count: int = 0) -> str:
    """
    Render a star rating display.

    Args:
        average_rating: The average rating (1-5 scale), or None if no ratings
        rating_count: The number of ratings

    Returns:
        HTML string with star rating display

    """
    if average_rating is None or rating_count == 0:
        return mark_safe(
            '<span class="text-sm text-subtle">No ratings yet</span>',
        )

    # Round to nearest 0.5
    rounded_rating = round(average_rating * 2) / 2

    stars_html = '<div class="flex items-center gap-1">'

    # Generate star icons
    full_stars = int(rounded_rating)
    half_star = rounded_rating - full_stars >= HALF_STAR_THRESHOLD

    # Full stars
    for _ in range(full_stars):
        stars_html += (
            f'<svg class="h-4 w-4 text-yellow-400 fill-current" '
            f'viewBox="0 0 20 20"><path d="{STAR_PATH}"/></svg>'
        )

    # Half star
    if half_star:
        stars_html += (
            '<svg class="h-4 w-4 text-yellow-400" viewBox="0 0 20 20">'
            '<defs><linearGradient id="half">'
            '<stop offset="50%" stop-color="currentColor"/>'
            '<stop offset="50%" stop-color="rgb(209 213 219)" stop-opacity="1"/>'
            "</linearGradient></defs>"
            f'<path fill="url(#half)" d="{STAR_PATH}"/></svg>'
        )

    # Empty stars
    empty_stars = 5 - full_stars - (1 if half_star else 0)
    for _ in range(empty_stars):
        stars_html += (
            f'<svg class="h-4 w-4 text-gray-300 fill-current" '
            f'viewBox="0 0 20 20"><path d="{STAR_PATH}"/></svg>'
        )

    # Add rating text
    stars_html += (
        f'<span class="text-sm text-muted ml-1">{average_rating:.1f} ({rating_count})</span>'
    )
    stars_html += "</div>"

    return mark_safe(stars_html)  # noqa: S308
