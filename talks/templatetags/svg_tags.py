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
# ruff: noqa: S308

from pathlib import Path

from django import template
from django.conf import settings
from django.utils.safestring import SafeString, mark_safe


register = template.Library()


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
    svg_path = Path(settings.BASE_DIR) / "static" / "images" / "icons" / f"{name}.svg"

    try:
        svg_content = svg_path.read_text()

        # Add CSS classes if provided
        if css_class:
            svg_content = svg_content.replace("<svg", f'<svg class="{css_class}"')

        return mark_safe(svg_content)

    except (OSError, FileNotFoundError):
        # Return empty string if file doesn't exist or can't be read
        return mark_safe("")
