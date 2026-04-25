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

from functools import lru_cache
from pathlib import Path

from django import template
from django.conf import settings
from django.utils.safestring import SafeString, mark_safe


register = template.Library()


@lru_cache(maxsize=256)
def _read_svg(svg_path: str, css_class: str) -> str:
    """Read and cache an SVG file, optionally injecting CSS classes."""
    try:
        svg_content = Path(svg_path).read_text()
    except OSError:
        return ""
    else:
        if css_class:
            svg_content = svg_content.replace("<svg", f'<svg class="{css_class}"')
        return svg_content


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
    svg_dir = Path(settings.BASE_DIR) / "svg"
    svg_path = (svg_dir / f"{name}.svg").resolve()
    # Guard against path traversal (e.g. name="../../etc/passwd")
    if not svg_path.is_relative_to(svg_dir.resolve()):
        return mark_safe("")  # nosec: B308
    return mark_safe(_read_svg(str(svg_path), css_class))  # nosec: B308, B703  # noqa: S308
