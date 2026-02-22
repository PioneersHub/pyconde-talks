"""Typed import context - Parameter Object for the Pretalx importer."""

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from talks.management.commands._pretalx.types import LogFn, VerbosityLevel


if TYPE_CHECKING:
    from events.models import Event


@dataclass(frozen=True)
class ImportContext:
    """
    Immutable, typed context shared across the entire import pipeline.

    Provides a convenience :meth:`log` that eliminates the need to pass *verbosity* on every call.
    """

    verbosity: VerbosityLevel
    log_fn: LogFn
    dry_run: bool = False
    no_update: bool = False
    skip_images: bool = False
    image_format: str = "webp"
    max_retries: int = 3
    pretalx_event_url: str = ""
    event_slug: str = ""
    event_name: str = ""
    api_token: str = ""
    event_obj: Event | None = None

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def log(
        self,
        message: str,
        min_level: VerbosityLevel,
        style: str | None = None,
    ) -> None:
        """
        Emit *message* when ``self.verbosity >= min_level``.

        Delegates to the :attr:`log_fn` callback supplied at construction.
        """
        self.log_fn(message, self.verbosity, min_level, style)

    def evolve(self, **changes: Any) -> ImportContext:
        """Return a shallow copy with *changes* applied (frozen-dataclass update)."""
        return dataclasses.replace(self, **changes)

    @classmethod
    def from_options(cls, options: dict[str, Any], *, log_fn: LogFn) -> ImportContext:
        """Construct from Django's parsed ``options`` dict (as passed to ``handle()``)."""
        return cls(
            verbosity=VerbosityLevel(options["verbosity"]),
            log_fn=log_fn,
            dry_run=options.get("dry_run", False),
            no_update=options.get("no_update", False),
            skip_images=options.get("skip_images", False),
            image_format=options.get("image_format", "webp") or "webp",
            max_retries=options.get("max_retries", 3),
            pretalx_event_url=options.get("pretalx_event_url", ""),
            event_slug=options.get("event_slug", ""),
            event_name=options.get("event_name", ""),
            api_token=options.get("api_token", ""),
        )
