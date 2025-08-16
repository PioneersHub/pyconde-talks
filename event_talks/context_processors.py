"""Template context processors for site-wide branding and event configuration."""

from typing import Any

from django.conf import settings


def branding(_: Any) -> dict[str, Any]:
    """Inject branding and event-related variables into all templates."""
    event_name = getattr(settings, "BRAND_EVENT_NAME", "Event")
    event_year = getattr(settings, "BRAND_EVENT_YEAR", "")
    full_name = f"{event_name} {event_year}".strip()

    pretalx_base = getattr(settings, "PRETALX_BASE_URL", "https://pretalx.com").rstrip("/")
    pretalx_slug = getattr(settings, "PRETALX_EVENT_SLUG", "").strip("/")

    pretalx_event_base = f"{pretalx_base}/{pretalx_slug}" if pretalx_slug else ""
    pretalx_schedule_url = f"{pretalx_event_base}/schedule/" if pretalx_event_base else ""
    pretalx_speakers_url = f"{pretalx_event_base}/speaker/" if pretalx_event_base else ""

    brand_title = f"{full_name} Talks" if full_name else "Talks"
    meta_description = f"{full_name} Talks and Schedule" if full_name else "Talks and Schedule"

    return {
        "brand_event_name": event_name,
        "brand_event_year": event_year,
        "brand_full_name": full_name,
        "brand_title": brand_title,
        "brand_meta_description": meta_description,
        "brand_main_website_url": getattr(settings, "BRAND_MAIN_WEBSITE_URL", ""),
        "brand_venue_url": getattr(settings, "BRAND_VENUE_URL", ""),
        "brand_logo_svg_name": getattr(settings, "BRAND_LOGO_SVG_NAME", ""),
        "brand_made_by_name": getattr(settings, "BRAND_MADE_BY_NAME", ""),
        "brand_made_by_url": getattr(settings, "BRAND_MADE_BY_URL", ""),
        "pretalx_event_slug": pretalx_slug,
        "pretalx_schedule_url": pretalx_schedule_url,
        "pretalx_speakers_url": pretalx_speakers_url,
    }
