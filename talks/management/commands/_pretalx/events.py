"""
Event resolution, creation, and name synchronization helpers.

Responsible for mapping CLI flags (``--event-slug``, ``--event-name``, ``--pretalx-event-url``) to a
Django :class:`~events.models.Event` row.
"""

from typing import TYPE_CHECKING

from django.utils import timezone

from events.models import Event
from talks.management.commands._pretalx.types import VerbosityLevel


if TYPE_CHECKING:
    from pytanis import PretalxClient

    from talks.management.commands._pretalx.context import ImportContext


def resolve_event_slug(ctx: ImportContext) -> str | None:
    """
    Derive the event slug from CLI args or the Pretalx URL.

    Returns ``None`` (and logs an error) when neither source is available,
    which signals the caller to abort the import.
    """
    if ctx.event_slug:
        return ctx.event_slug

    if ctx.pretalx_event_url:
        slug = ctx.pretalx_event_url.rstrip("/").split("/")[-1]
        ctx.log(
            f"No event slug provided, derived from Pretalx URL: '{slug}'",
            VerbosityLevel.NORMAL,
            "WARNING",
        )
        return slug

    ctx.log(
        "No event slug provided and no Pretalx event URL provided. Cannot proceed.",
        VerbosityLevel.NORMAL,
        "ERROR",
    )
    return None


def get_or_create_event(
    event_slug: str,
    ctx: ImportContext,
) -> tuple[Event, bool]:
    """
    Get or create the :class:`~events.models.Event` for *event_slug*.

    Returns a ``(event, created)`` tuple.
    """
    event_obj, created = Event.objects.get_or_create(
        slug=event_slug,
        defaults={
            "name": ctx.event_name or event_slug,
            "year": timezone.now().year,
            "pretalx_url": ctx.pretalx_event_url,
        },
    )
    if created:
        ctx.log(
            f"Created new Event '{event_slug}'",
            VerbosityLevel.NORMAL,
            "SUCCESS",
        )
    return event_obj, created


def resolve_pretalx_url(
    pretalx_event_url: str,
    event_obj: Event,
    event_slug: str,
) -> str:
    """
    Resolve the Pretalx event URL.

    Priority: CLI flag > ``Event.pretalx_url`` field > default ``pretalx.com``.
    """
    if pretalx_event_url:
        return pretalx_event_url
    return event_obj.pretalx_url or f"https://pretalx.com/{event_slug}"


def split_pretalx_url(pretalx_event_url: str) -> tuple[str, str]:
    """Split a Pretalx event URL into ``(base_url, event_slug)``."""
    base_url, event_slug = pretalx_event_url.rstrip("/").rsplit("/", 1)
    return base_url, event_slug


def maybe_update_event_name(
    pretalx_client: PretalxClient,
    pretalx_event_slug: str,
    event_obj: Event,
    ctx: ImportContext,
    *,
    created: bool,
) -> str:
    """
    Fetch the event name from the API and update the ``Event`` row when freshly created.

    Returns the fetched event name (may be empty if unavailable).
    """
    event_name = ""
    event = pretalx_client.event(pretalx_event_slug)
    if hasattr(event, "name") and event.name and hasattr(event.name, "en") and event.name.en:
        event_name = event.name.en

    ctx.log(
        f"Fetched event name from Pretalx API: '{event_name}'",
        VerbosityLevel.NORMAL,
    )

    if created and event_name and event_name != event_obj.slug:
        event_obj.name = event_name
        event_obj.save()
        ctx.log(
            f"Updated Event name to '{event_name}'",
            VerbosityLevel.NORMAL,
            "SUCCESS",
        )

    return event_name
