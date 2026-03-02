"""Template context processors for site-wide branding and event configuration."""

from typing import TYPE_CHECKING, Any

from django.conf import settings

from events.models import Event
from users.models import CustomUser


if TYPE_CHECKING:
    from django.http import HttpRequest


def _get_current_event(request: HttpRequest) -> Event | None:
    """
    Return the current event from the request user or the default event setting.

    Resolution order for authenticated users:
    1. Event slug stored in the session (set during login).
    2. The DEFAULT_EVENT setting.
    3. First active event the user is associated with.

    For anonymous users the DEFAULT_EVENT setting is used, then the first active event.
    """
    # Preferred slug: session selection first, then DEFAULT_EVENT.
    session_slug: str = getattr(request, "session", {}).get("selected_event_slug", "")
    default_slug: str = getattr(settings, "DEFAULT_EVENT", "")

    if hasattr(request, "user") and request.user.is_authenticated:
        user = request.user
        if isinstance(user, CustomUser):
            # Try session-selected event first (must belong to the user).
            if session_slug:
                event = user.events.filter(slug=session_slug, is_active=True).first()
                if event:
                    return event

            # Fall back to DEFAULT_EVENT if the user is associated with it.
            if default_slug:
                event = user.events.filter(slug=default_slug, is_active=True).first()
                if event:
                    return event

            # Last resort: any active event for this user.
            event = user.events.filter(is_active=True).first()
            if event:
                return event

    if default_slug:
        return Event.objects.filter(slug=default_slug).first()

    return Event.objects.filter(is_active=True).first()


def branding(request: HttpRequest) -> dict[str, Any]:
    """Inject branding and event-related variables into all templates."""
    event = _get_current_event(request)

    if event is None:
        return {
            "brand_event_name": "",
            "brand_event_year": "",
            "brand_title": "Talks",
            "brand_meta_description": "Talks and Schedule",
            "brand_main_website_url": "",
            "brand_venue_url": "",
            "brand_logo_svg_name": "",
            "brand_assets_subdir": "",
            "brand_made_by_name": "",
            "brand_made_by_url": "",
            "pretalx_schedule_url": "",
            "pretalx_speakers_url": "",
        }

    event_name = event.name
    event_year = str(event.year) if event.year else ""
    prefix = f"{event_name} " if event_name else ""

    return {
        "brand_event_name": event_name,
        "brand_event_year": event_year,
        "brand_title": f"{prefix}Talks",
        "brand_meta_description": f"{prefix}Talks and Schedule",
        "brand_main_website_url": event.main_website_url,
        "brand_venue_url": event.venue_url,
        "brand_logo_svg_name": event.logo_svg_name,
        "brand_assets_subdir": event.slug,
        "brand_made_by_name": event.made_by_name,
        "brand_made_by_url": event.made_by_url,
        "pretalx_schedule_url": event.pretalx_schedule_url,
        "pretalx_speakers_url": event.pretalx_speakers_url,
    }
