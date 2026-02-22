"""Event management module for multi-event support."""

from typing import TYPE_CHECKING, ClassVar

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


if TYPE_CHECKING:
    from django_stubs_ext.db.models.manager import RelatedManager

    from talks.models import Talk
    from users.models import CustomUser

MAX_EVENT_NAME_LENGTH = 200
MAX_EVENT_SLUG_LENGTH = 100
MAX_FIELD_LENGTH = 200


class Event(models.Model):
    """Represents a conference event (e.g., PyConDE & PyData 2026)."""

    name = models.CharField(
        unique=True,
        max_length=MAX_EVENT_NAME_LENGTH,
        help_text=_("Display name of the event. Include the year if applicable."),
    )

    slug = models.SlugField(
        max_length=MAX_EVENT_SLUG_LENGTH,
        unique=True,
        null=False,
        blank=False,
        help_text=_(
            "Event slug. Name used in URLs and for assets/media organization. "
            "Not necessarily the same as the Pretalx slug.",
        ),
    )

    year = models.PositiveSmallIntegerField(
        _("Event year"),
        null=True,
        blank=True,
        validators=[
            MinValueValidator(2000),
            MaxValueValidator(2100),
        ],
    )

    validation_api_url = models.URLField(
        blank=True,
        default="",
        help_text=_(
            "URL of the external API used to validate if a user bought a ticket for this event. "
            "Leave blank to disable API validation for this event.",
        ),
    )

    is_active = models.BooleanField(
        default=True,
        help_text=_("Whether this event is currently active and visible on the site"),
    )

    # ------------------------------------------------------------------
    # Branding / display fields
    # ------------------------------------------------------------------

    main_website_url = models.URLField(
        blank=True,
        default="",
        help_text=_("Main website URL for the event"),
    )

    venue_url = models.URLField(
        blank=True,
        default="",
        help_text=_("Venue information URL"),
    )

    logo_svg_name = models.CharField(
        max_length=MAX_FIELD_LENGTH,
        blank=True,
        default="",
        help_text=_("Name of the SVG logo file (without extension)"),
    )

    made_by_name = models.CharField(
        max_length=MAX_FIELD_LENGTH,
        blank=True,
        default="",
        help_text=_("Name of the organizing team or community"),
    )

    made_by_url = models.URLField(
        blank=True,
        default="",
        help_text=_("URL linking to the organizer or community page"),
    )

    # ------------------------------------------------------------------
    # Pretalx
    # ------------------------------------------------------------------
    pretalx_url = models.URLField(
        blank=True,
        default="",
        help_text=_("Pretalx event base URL (e.g., 'https://pretalx.com/my-event')"),
    )

    if TYPE_CHECKING:
        talks: RelatedManager[Talk]
        users: RelatedManager[CustomUser]

    class Meta:
        """Metadata for the Event model."""

        verbose_name = _("Event")
        verbose_name_plural = _("Events")
        ordering: ClassVar[list[str]] = ["name"]

    def __str__(self) -> str:
        """Return the event name."""
        return self.name

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def pretalx_schedule_url(self) -> str:
        """Return the Pretalx schedule URL for this event."""
        event_base = self.pretalx_url
        return f"{event_base}/schedule/" if event_base else ""

    @property
    def pretalx_speakers_url(self) -> str:
        """Return the Pretalx speakers URL for this event."""
        event_base = self.pretalx_url
        return f"{event_base}/speaker/" if event_base else ""
