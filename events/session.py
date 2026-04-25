"""
Helpers for tracking the user-selected event across requests.

Several views and the branding context processor need to know which ``Event`` the current
request should be bound to. They all agree on the same session key and the same fallback
order (session slug, then the ``DEFAULT_EVENT`` setting, then the first active event), so
the key and the resolution helper live in one place rather than being copied at each call
site.
"""

from typing import TYPE_CHECKING

from django.conf import settings

from events.models import Event


if TYPE_CHECKING:
    from django.http import HttpRequest


SESSION_EVENT_SLUG_KEY = "selected_event_slug"


def get_selected_event_slug(request: HttpRequest) -> str:
    """Return the event slug currently saved in the session, or an empty string."""
    slug: str = getattr(request, "session", {}).get(SESSION_EVENT_SLUG_KEY, "")
    return slug


def set_selected_event_slug(request: HttpRequest, slug: str) -> None:
    """Persist the given event slug in the session (no-op if the request has no session)."""
    if hasattr(request, "session"):
        request.session[SESSION_EVENT_SLUG_KEY] = slug


def resolve_default_event(request: HttpRequest) -> Event | None:
    """
    Resolve the current event without scoping to a specific user.

    The resolution order is:
    1. The active event whose slug matches ``SESSION_EVENT_SLUG_KEY`` in the session.
    2. The active event whose slug matches the ``DEFAULT_EVENT`` setting.
    3. Any active event, as a last-resort fallback.

    This is the version used for list views and other places that treat "the current event"
    as a site-wide default. The ``branding`` context processor has its own user-scoped
    variant because it needs to respect per-user event membership.
    """
    session_slug = get_selected_event_slug(request)
    default_slug: str = getattr(settings, "DEFAULT_EVENT", "")

    for slug in (session_slug, default_slug):
        if slug:
            event = Event.objects.filter(slug=slug, is_active=True).first()
            if event:
                return event

    return Event.objects.filter(is_active=True).first()
