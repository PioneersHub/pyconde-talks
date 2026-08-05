"""Tests for the Event model."""

import pytest
from django.db import IntegrityError
from model_bakery import baker

from events.models import (
    MAX_EVENT_NAME_LENGTH,
    MAX_EVENT_SLUG_LENGTH,
    MAX_FIELD_LENGTH,
    PUBLICLY_LISTED_VISIBILITIES,
    Event,
)
from talks.models import Talk
from users.models import CustomUser


@pytest.mark.django_db
class TestEventModel:
    """Tests for Event model CRUD, constraints, and __str__."""

    def test_create_event(self) -> None:
        """Create an event with all fields and verify it is stored correctly."""
        event = Event.objects.create(
            name="PyConDE & PyData Berlin 2025",
            slug="pyconde-pydata-2025",
            year=2025,
            validation_api_url="https://api.example.com/validate",
            is_active=True,
        )
        assert event.pk is not None
        assert event.name == "PyConDE & PyData Berlin 2025"
        assert event.slug == "pyconde-pydata-2025"
        assert event.year == 2025  # noqa: PLR2004
        assert event.validation_api_url == "https://api.example.com/validate"
        assert event.is_active is True

    def test_str_returns_name(self) -> None:
        """__str__ returns the event name."""
        event = baker.make(Event, name="My Event 2025")
        assert str(event) == "My Event 2025"

    def test_slug_unique_constraint(self) -> None:
        """Two events with the same slug raise IntegrityError."""
        Event.objects.create(name="Event A", slug="same-slug", year=2025)
        with pytest.raises(IntegrityError):
            Event.objects.create(name="Event B", slug="same-slug", year=2025)

    def test_default_is_active_true(self) -> None:
        """is_active defaults to True when not explicitly set."""
        event = Event.objects.create(name="Active Event", slug="active", year=2025)
        assert event.is_active is True

    def test_validation_api_url_blank_default(self) -> None:
        """validation_api_url defaults to an empty string."""
        event = Event.objects.create(name="No API", slug="no-api", year=2025)
        assert event.validation_api_url == ""

    def test_show_rating_summary_default_true(self) -> None:
        """show_rating_summary defaults to True."""
        event = Event.objects.create(name="Rating Event", slug="rating-ev", year=2025)
        assert event.show_rating_summary is True

    def test_max_name_length(self) -> None:
        """Verify MAX_EVENT_NAME_LENGTH constant exists and is reasonable."""
        assert MAX_EVENT_NAME_LENGTH == 200  # noqa: PLR2004

    def test_max_slug_length(self) -> None:
        """Verify MAX_EVENT_SLUG_LENGTH constant exists and is reasonable."""
        assert MAX_EVENT_SLUG_LENGTH == 100  # noqa: PLR2004

    def test_verbose_name(self) -> None:
        """Meta verbose_name is 'Event'."""
        assert Event._meta.verbose_name == "Event"
        assert Event._meta.verbose_name_plural == "Events"

    def test_talks_related_manager(self) -> None:
        """Event.talks reverse relation returns associated talks."""
        event = Event.objects.create(name="Ev", slug="ev", year=2025)
        talk = baker.make(Talk, event=event, title="A Talk")
        assert talk in event.talks.all()

    def test_users_related_manager(self) -> None:
        """Event.users reverse relation returns associated users."""
        event = Event.objects.create(name="Ev", slug="ev-users", year=2025)
        user = baker.make(CustomUser, email="e@example.com")
        user.events.add(event)
        assert user in event.users.all()


@pytest.mark.django_db
class TestEventBrandingProperties:
    """Tests for Event model branding and Pretalx derived properties."""

    def test_pretalx_schedule_url(self) -> None:
        """pretalx_schedule_url appends /schedule/ to event base URL."""
        event = Event.objects.create(
            name="E",
            slug="e",
            pretalx_url="https://pretalx.com/my-event",
        )
        assert event.pretalx_schedule_url == "https://pretalx.com/my-event/schedule/"

    def test_pretalx_speakers_url(self) -> None:
        """pretalx_speakers_url appends /speaker/ to event base URL."""
        event = Event.objects.create(
            name="E",
            slug="e",
            pretalx_url="https://pretalx.com/my-event",
        )
        assert event.pretalx_speakers_url == "https://pretalx.com/my-event/speaker/"

    def test_max_field_length(self) -> None:
        """Verify MAX_FIELD_LENGTH constant."""
        assert MAX_FIELD_LENGTH == 200  # noqa: PLR2004

    def test_branding_fields_blank_default(self) -> None:
        """All branding fields default to empty strings."""
        event = Event.objects.create(
            name="E",
            slug="e",
            pretalx_url="https://pretalx.com/bare",
        )
        assert event.main_website_url == ""
        assert event.venue_url == ""
        assert event.logo_svg_name == ""
        assert event.made_by_name == ""
        assert event.made_by_url == ""


@pytest.mark.django_db
class TestEventVisibility:
    """Tests for the ``visibility`` field and the helpers derived from it."""

    def test_defaults_to_hidden(self) -> None:
        """A new event keeps the whole login wall until someone opens it deliberately."""
        event = Event.objects.create(name="E", slug="e")
        assert event.visibility == Event.Visibility.HIDDEN

    def test_publicly_listed_visibilities_matches_the_enum(self) -> None:
        """
        The queryset constant stays in step with the enum.

        ``PUBLICLY_LISTED_VISIBILITIES`` holds plain strings so it can go straight into a
        filter, which means a renamed or added choice would otherwise drift from it silently.
        """
        assert set(PUBLICLY_LISTED_VISIBILITIES) == {
            str(Event.Visibility.SCHEDULE_ONLY),
            str(Event.Visibility.PUBLIC),
        }
        assert str(Event.Visibility.HIDDEN) not in PUBLICLY_LISTED_VISIBILITIES

    @pytest.mark.parametrize(
        ("visibility", "listed", "videos"),
        [
            (Event.Visibility.HIDDEN, False, False),
            (Event.Visibility.SCHEDULE_ONLY, True, False),
            (Event.Visibility.PUBLIC, True, True),
        ],
    )
    def test_derived_flags(
        self,
        visibility: str,
        listed: bool,  # noqa: FBT001
        videos: bool,  # noqa: FBT001
    ) -> None:
        """Listing and recording access follow the visibility state independently."""
        event = baker.make(Event, visibility=visibility)
        assert event.is_publicly_listed is listed
        assert event.videos_are_public is videos
