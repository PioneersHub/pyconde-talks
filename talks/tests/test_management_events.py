"""Tests for event support in management commands (generate_fake_talks & import_pretalx_talks)."""
# ruff: noqa: SLF001 PLR2004

from datetime import timedelta
from io import StringIO
from typing import Any
from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.management.commands.import_pretalx_talks import Command as ImportCommand
from talks.models import Talk


# ---------------------------------------------------------------------------
# generate_fake_talks event support
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateFakeTalksEvent:
    """Verify generate_fake_talks creates/reuses events and links talks."""

    def test_creates_event_from_slug(self) -> None:
        """Command creates a new Event when the slug doesn't exist yet."""
        out = StringIO()
        call_command(
            "generate_fake_talks",
            count="2",
            seed="42",
            event="brand-new-event",
            stdout=out,
        )
        assert Event.objects.filter(slug="brand-new-event").exists()
        # All generated talks should be linked to this event
        event = Event.objects.get(slug="brand-new-event")
        assert Talk.objects.filter(event=event).count() == 2

    def test_reuses_existing_event(self) -> None:
        """Command reuses an existing Event when the slug already exists."""
        existing = Event.objects.create(name="Existing", slug="existing-event", year=2025)
        out = StringIO()
        call_command(
            "generate_fake_talks",
            count="1",
            seed="42",
            event="existing-event",
            stdout=out,
        )
        assert Event.objects.filter(slug="existing-event").count() == 1
        assert Talk.objects.filter(event=existing).count() == 1

    def test_no_event_flag_no_event_linked(self) -> None:
        """When --event is empty, talks are created without an event."""
        out = StringIO()
        call_command(
            "generate_fake_talks",
            count="1",
            seed="42",
            event="",
            stdout=out,
        )
        assert Talk.objects.filter(event__isnull=True).count() == 1


# ---------------------------------------------------------------------------
# import_pretalx_talks event support
# ---------------------------------------------------------------------------


@pytest.fixture()
def import_command() -> ImportCommand:
    """Create an ImportCommand instance with mocked stdout/stderr."""
    cmd = ImportCommand()
    cmd.stdout = StringIO()  # type: ignore[assignment]
    cmd.stderr = StringIO()  # type: ignore[assignment]
    return cmd


def _make_submission_data_mock(
    *,
    title: str = "Test Talk",
    code: str = "TST001",
) -> Mock:
    """Create a Mock that behaves like SubmissionData."""
    data = Mock()
    data.code = code
    data.title = title
    data.abstract = "Abstract"
    data.description = "Description"
    data.start_time = timezone.now()
    data.duration = timedelta(minutes=30)
    data.room = None
    data.track = "Python"
    data.pretalx_link = f"https://pretalx.com/ev/talk/{code}"
    data.image_url = ""
    data.submission_type = "Talk"
    return data


@pytest.mark.django_db
class TestImportPretalxTalksEvent:
    """Verify import_pretalx_talks creates/reuses events and links talks."""

    def test_handle_creates_event(self) -> None:
        """Event resolution creates a new Event for an unknown slug."""
        event_obj, created = Event.objects.get_or_create(
            slug="new-import-event",
            defaults={"name": "new-import-event", "year": 2025},
        )
        assert created is True
        assert event_obj.slug == "new-import-event"

    def test_handle_reuses_existing_event(self) -> None:
        """Event resolution reuses an existing Event for a known slug."""
        existing = Event.objects.create(name="Import Event", slug="import-event", year=2025)
        event_obj, created = Event.objects.get_or_create(
            slug="import-event",
            defaults={"name": "import-event", "year": 2025},
        )
        assert created is False
        assert event_obj.pk == existing.pk

    def test_create_talk_sets_event(self, import_command: ImportCommand) -> None:
        """_create_talk passes event to Talk.objects.create."""
        event = Event.objects.create(name="Ev", slug="ev-create", year=2025)
        data = _make_submission_data_mock(title="New Import Talk", code="CRT001")

        options: dict[str, Any] = {
            "event": "ev-create",
            "verbosity": 1,
            "max_retries": 1,
            "_event_obj": event,
        }

        talk = import_command._create_talk(data=data, options=options)
        assert talk.event == event

    def test_update_talk_sets_event(self, import_command: ImportCommand) -> None:
        """_update_talk links an existing talk to the event if not already linked."""
        event = Event.objects.create(name="Ev", slug="ev-update", year=2025)
        talk = baker.make(Talk, title="Existing Talk", event=None)

        data = _make_submission_data_mock(title="Updated Talk", code="UPD001")

        options: dict[str, Any] = {
            "event": "ev-update",
            "verbosity": 1,
            "max_retries": 1,
            "_event_obj": event,
        }

        # Mock _update_talk_speakers to avoid speaker creation side-effects
        with patch.object(import_command, "_update_talk_speakers"):
            import_command._update_talk(
                talk=talk,
                data=data,
                speakers=[],
                options=options,
            )

        talk.refresh_from_db()
        assert talk.event == event
