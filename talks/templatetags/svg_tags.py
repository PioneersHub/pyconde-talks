"""
SVG template tags for Django applications.

This module provides a template tag to easily include SVG icons directly in Django templates.

It reads SVG files from the filesystem and outputs them inline with optional CSS classes, allowing
for direct styling and manipulation.

Usage:
    1. Load the tag library in your template:
       {% load svg_tags %}

    2. Use the svg tag to include an SVG file:
       {% svg 'icon-name' 'optional-css-classes' %}

Notes:
    - SVG files should be stored in static/images/icons/ directory
    - All SVG files are assumed to be safe and will be marked as safe HTML

"""

from pathlib import Path

from django import template
from django.conf import settings
from django.utils.safestring import SafeString, mark_safe


register = template.Library()

# SVG path constant for star icons (used by rating_tags)
STAR_PATH = (
    "M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 "
    "0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 "
    "1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-"
    "1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-"
    "1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"
)


@register.simple_tag
def svg(name: str, css_class: str = "") -> SafeString:
    """
    Render an SVG file inline in a Django template.

    Loads an SVG from the static/images/icons directory and injects it directly into the HTML with
    optional CSS classes for styling.

    Args:
        name: The filename of the SVG without the .svg extension
        css_class: Optional CSS classes to add to the SVG element

    Returns:
        SafeString: The SVG content marked as safe for HTML rendering, or an empty string if the
                    file is not found

    Examples:
        {% svg 'info' %}
        {% svg 'arrow' 'h-4 w-4 text-blue-500' %}

    """
    svg_path = Path(settings.BASE_DIR) / "svg" / f"{name}.svg"

    try:
        svg_content = svg_path.read_text()

        # Add CSS classes if provided
        if css_class:
            svg_content = svg_content.replace("<svg", f'<svg class="{css_class}"')

        return mark_safe(svg_content)  # nosec: B308, B703  # noqa: S308

    except OSError:
        # Return empty string if file doesn't exist or can't be read
        return mark_safe("")  # nosec: B308
