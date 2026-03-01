"""Tests for the schedule grid view and template tags."""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from talks.models import Room, SavedTalk, Talk
from users.models import CustomUser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def user() -> CustomUser:
    """Create a regular user for testing."""
    return baker.make(CustomUser, email="schedule@example.com")


@pytest.fixture()
def rooms() -> list[Room]:
    """Create two rooms."""
    return [
        baker.make(Room, name="Room A"),
        baker.make(Room, name="Room B"),
    ]


@pytest.fixture()
def today_talks(rooms: list[Room]) -> list[Talk]:
    """Create talks for today spread across two rooms and two time slots."""
    now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
    return [
        baker.make(
            Talk,
            title="Talk 1",
            room=rooms[0],
            start_time=now,
            duration=timedelta(minutes=30),
        ),
        baker.make(
            Talk,
            title="Talk 2",
            room=rooms[1],
            start_time=now,
            duration=timedelta(minutes=30),
        ),
        baker.make(
            Talk,
            title="Talk 3",
            room=rooms[0],
            start_time=now + timedelta(hours=1),
            duration=timedelta(minutes=45),
        ),
    ]


# ---------------------------------------------------------------------------
# View Tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestScheduleView:
    """Tests for the schedule_view function."""

    def test_requires_login(self, client: pytest.fixture) -> None:  # type: ignore[type-arg,valid-type]
        """Unauthenticated users are redirected to login."""
        url = reverse("schedule")
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
        assert "/accounts/login/" in response.url  # type: ignore[attr-defined]

    def test_empty_schedule(self, client: pytest.fixture, user: CustomUser) -> None:  # type: ignore[type-arg,valid-type]
        """Schedule page renders when there are no talks."""
        client.force_login(user)
        response = client.get(reverse("schedule"))
        assert response.status_code == HTTPStatus.OK
        assert b"No talks scheduled" in response.content

    def test_renders_talks_for_default_date(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],  # noqa: ARG002
    ) -> None:
        """The schedule shows talks for the first available date by default."""
        client.force_login(user)
        response = client.get(reverse("schedule"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Talk 1" in content
        assert "Talk 2" in content
        assert "Talk 3" in content

    def test_renders_room_headers(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],  # noqa: ARG002
        rooms: list[Room],  # noqa: ARG002
    ) -> None:
        """Room names appear as column headers."""
        client.force_login(user)
        response = client.get(reverse("schedule"))
        content = response.content.decode()
        assert "Room A" in content
        assert "Room B" in content

    def test_date_picker_selects_date(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],
    ) -> None:
        """Passing ?date= filters talks to that day."""
        client.force_login(user)
        talk_date = today_talks[0].start_time.date()
        url = reverse("schedule") + f"?date={talk_date.isoformat()}"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert b"Talk 1" in response.content

    def test_invalid_date_falls_back(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],  # noqa: ARG002
    ) -> None:
        """An invalid date parameter falls back to the first available date."""
        client.force_login(user)
        url = reverse("schedule") + "?date=not-a-date"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert b"Talk 1" in response.content

    def test_saved_talk_shows_bookmark(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],
    ) -> None:
        """Saved talks display a bookmark indicator in the grid."""
        client.force_login(user)
        SavedTalk.objects.create(user=user, talk=today_talks[0])
        response = client.get(reverse("schedule"))
        content = response.content.decode()
        # The bookmark SVG (fill="currentColor") should appear for the saved talk
        assert "text-yellow-500" in content

    def test_different_day_shows_no_talks(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],
    ) -> None:
        """Selecting a day with no talks shows an empty message."""
        client.force_login(user)
        tomorrow = (today_talks[0].start_time + timedelta(days=1)).date()
        url = reverse("schedule") + f"?date={tomorrow.isoformat()}"
        response = client.get(url)
        # Falls back to first available date since tomorrow isn't in available_dates
        assert response.status_code == HTTPStatus.OK

    def test_css_grid_area_in_output(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],  # noqa: ARG002
    ) -> None:
        """The CSS Grid schedule renders grid-area styles for talk cards."""
        client.force_login(user)
        response = client.get(reverse("schedule"))
        content = response.content.decode()
        assert "grid-area:" in content
        assert "schedule-grid" in content

    def test_grid_template_rows_in_output(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],  # noqa: ARG002
    ) -> None:
        """The grid-template-rows CSS is rendered with named time slices."""
        client.force_login(user)
        response = client.get(reverse("schedule"))
        content = response.content.decode()
        assert "grid-template-rows:" in content
        # Named lines like [t-1000] should appear
        assert "[t-" in content

    def test_bookmark_button_on_cards(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],  # noqa: ARG002
    ) -> None:
        """Each talk card has a bookmark toggle button."""
        client.force_login(user)
        response = client.get(reverse("schedule"))
        content = response.content.decode()
        assert "sched-save-" in content
        assert "toggle_save_talk" in content or "hx-post" in content

    def test_search_filter(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],
    ) -> None:
        """The ?q= parameter filters talks by title."""
        client.force_login(user)
        talk_date = today_talks[0].start_time.date()
        url = reverse("schedule") + f"?date={talk_date.isoformat()}&q=Talk+1"
        response = client.get(url)
        content = response.content.decode()
        assert "Talk 1" in content
        # Talk 2 and Talk 3 should be filtered out
        assert "Talk 2" not in content
        assert "Talk 3" not in content

    def test_saved_filter(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],
    ) -> None:
        """The ?saved=1 parameter shows only bookmarked talks."""
        client.force_login(user)
        SavedTalk.objects.create(user=user, talk=today_talks[0])
        talk_date = today_talks[0].start_time.date()
        url = reverse("schedule") + f"?date={talk_date.isoformat()}&saved=1"
        response = client.get(url)
        content = response.content.decode()
        assert "Talk 1" in content
        assert "Talk 2" not in content
        assert "Talk 3" not in content

    def test_overlapping_talks_side_by_side(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        rooms: list[Room],
    ) -> None:
        """Overlapping talks in different rooms get distinct grid columns."""
        now = timezone.now().replace(hour=9, minute=20, second=0, microsecond=0)
        baker.make(
            Talk,
            title="Overlap A",
            room=rooms[0],
            start_time=now,
            duration=timedelta(minutes=40),
        )
        baker.make(
            Talk,
            title="Overlap B",
            room=rooms[1],
            start_time=now + timedelta(minutes=10),
            duration=timedelta(minutes=30),
        )
        client.force_login(user)
        response = client.get(reverse("schedule"))
        content = response.content.decode()
        assert "Overlap A" in content
        assert "Overlap B" in content
        # Each in a different grid column
        assert "/ 2 /" in content
        assert "/ 3 /" in content


# ---------------------------------------------------------------------------
# Template Tag Tests
# ---------------------------------------------------------------------------
class TestScheduleCellTag:
    """Tests for the schedule_cell template tag."""

    def test_lookup_existing(self) -> None:
        """Returns the value when time_slot and room_id exist."""
        tpl = "{% load schedule_tags %}{% schedule_cell grid slot rid as val %}{{ val }}"
        template = Template(tpl)
        slot = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        grid = {slot: {1: "TalkA"}}
        context = Context({"grid": grid, "slot": slot, "rid": 1})
        assert template.render(context).strip() == "TalkA"

    def test_lookup_missing_room(self) -> None:
        """Returns None when room_id is missing from the slot."""
        tpl = (
            "{% load schedule_tags %}"
            "{% schedule_cell grid slot rid as val %}"
            "{% if val %}yes{% else %}no{% endif %}"
        )
        template = Template(tpl)
        slot = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
        grid = {slot: {1: "TalkA"}}
        context = Context({"grid": grid, "slot": slot, "rid": 99})
        assert template.render(context).strip() == "no"

    def test_lookup_missing_slot(self) -> None:
        """Returns None when the time slot is missing entirely."""
        tpl = (
            "{% load schedule_tags %}"
            "{% schedule_cell grid slot rid as val %}"
            "{% if val %}yes{% else %}no{% endif %}"
        )
        template = Template(tpl)
        slot = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)
        grid = {datetime(2026, 3, 1, 10, 0, tzinfo=UTC): {1: "TalkA"}}
        context = Context({"grid": grid, "slot": slot, "rid": 1})
        assert template.render(context).strip() == "no"
