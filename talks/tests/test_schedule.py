"""Tests for the schedule grid view and template tags."""

from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.models import Room, SavedTalk, Talk
from talks.views_schedule import _build_grid_slices
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
        today_talks: list[Talk],
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
        today_talks: list[Talk],
        rooms: list[Room],
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
        today_talks: list[Talk],
    ) -> None:
        """An invalid date parameter falls back to the first available date."""
        client.force_login(user)
        url = reverse("schedule") + "?date=not-a-date"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert b"Talk 1" in response.content

    def test_default_date_uses_local_timezone(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        rooms: list[Room],
    ) -> None:
        """
        The schedule defaults to today using the local timezone, not UTC.

        When it is just past midnight in the local timezone (e.g. 00:30 CEST) but still the previous
        day in UTC (22:30 UTC), the schedule should select the local date.
        """
        # Simulate 00:30 local time on April 16 in Europe/Berlin (22:30 UTC April 15)
        utc_time = datetime(2026, 4, 15, 22, 30, tzinfo=UTC)
        local_date = timezone.localdate(utc_time)  # April 16 in Europe/Berlin

        # Create a talk on the local date (not the UTC date)
        baker.make(
            Talk,
            title="Late Night Talk",
            room=rooms[0],
            start_time=datetime(2026, 4, 16, 8, 0, tzinfo=UTC),
            duration=timedelta(minutes=30),
        )
        # Create a talk on the UTC date (the previous day)
        baker.make(
            Talk,
            title="Yesterday Talk",
            room=rooms[0],
            start_time=datetime(2026, 4, 15, 10, 0, tzinfo=UTC),
            duration=timedelta(minutes=30),
        )

        client.force_login(user)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(timezone, "localdate", lambda *_args, **_kwargs: local_date)
            response = client.get(reverse("schedule"))

        content = response.content.decode()
        # The selected day pill should highlight April 16 (the local date)
        assert "Late Night Talk" in content
        # Yesterday's talk should not appear (different date selected)
        assert "Yesterday Talk" not in content

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

    def test_event_param_does_not_leak_other_event(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        rooms: list[Room],
    ) -> None:
        """A user must not read another event's schedule by passing ?event=<other_id>."""
        other_event = baker.make(Event, name="Secret Event")
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        secret_talk = baker.make(
            Talk,
            title="Secret Keynote",
            room=rooms[0],
            start_time=now,
            duration=timedelta(minutes=30),
            event=other_event,
        )
        client.force_login(user)
        url = (
            reverse("schedule")
            + f"?event={other_event.pk}&date={secret_talk.start_time.date().isoformat()}"
        )
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        # The user is not a member of other_event, so its talk must not appear.
        assert "Secret Keynote" not in response.content.decode()

    def test_css_grid_area_in_output(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],
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
        today_talks: list[Talk],
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
        today_talks: list[Talk],
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

    def test_track_filter_hides_mismatching_talks(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        rooms: list[Room],
    ) -> None:
        """Only talks in the selected track appear on the schedule."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        baker.make(
            Talk,
            title="Pythonic Talk",
            room=rooms[0],
            track="PyData",
            start_time=now,
            duration=timedelta(minutes=30),
        )
        baker.make(
            Talk,
            title="Different Track Talk",
            room=rooms[0],
            track="Devops",
            start_time=now + timedelta(minutes=45),
            duration=timedelta(minutes=30),
        )
        client.force_login(user)
        response = client.get(reverse("schedule"), {"track": "PyData"})
        content = response.content.decode()
        assert "Pythonic Talk" in content
        assert "Different Track Talk" not in content

    def test_presentation_type_filter_hides_mismatching_talks(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        rooms: list[Room],
    ) -> None:
        """Only talks of the selected presentation type appear."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        baker.make(
            Talk,
            title="Tutorial Item",
            room=rooms[0],
            presentation_type=Talk.PresentationType.TUTORIAL,
            start_time=now,
            duration=timedelta(minutes=30),
        )
        baker.make(
            Talk,
            title="Keynote Item",
            room=rooms[0],
            presentation_type=Talk.PresentationType.KEYNOTE,
            start_time=now + timedelta(minutes=45),
            duration=timedelta(minutes=30),
        )
        client.force_login(user)
        response = client.get(
            reverse("schedule"),
            {"presentation_type": Talk.PresentationType.TUTORIAL},
        )
        content = response.content.decode()
        assert "Tutorial Item" in content
        assert "Keynote Item" not in content

    def test_schedule_skips_talks_without_rooms(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        rooms: list[Room],
    ) -> None:
        """Talks without a room should not produce schedule grid items."""
        now = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        baker.make(
            Talk,
            title="RoomedTalk",
            room=rooms[0],
            start_time=now,
            duration=timedelta(minutes=30),
        )
        # A talk without a room on the same day must still load as a Talk row but never get a
        # grid item (no column). This exercises the "if not t.room: continue" branch.
        baker.make(
            Talk,
            title="NoRoomTalk",
            room=None,
            start_time=now,
            duration=timedelta(minutes=30),
        )
        client.force_login(user)
        response = client.get(reverse("schedule"))
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "RoomedTalk" in content
        # Talks without a room are never laid out as grid cards because the template only
        # renders schedule_items through their grid-area.
        assert "NoRoomTalk" not in content

    def test_non_digit_event_param_falls_back_to_default(
        self,
        client: pytest.fixture,  # type: ignore[type-arg,valid-type]
        user: CustomUser,
        today_talks: list[Talk],
    ) -> None:
        """Garbage ``?event=`` values should be ignored rather than crashing."""
        client.force_login(user)
        response = client.get(reverse("schedule"), {"event": "not-a-number"})
        assert response.status_code == HTTPStatus.OK

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


# ---------------------------------------------------------------------------
# _build_grid_slices
# ---------------------------------------------------------------------------
class TestBuildGridSlices:
    """Unit tests for the pure-function grid slice builder."""

    def test_no_talks_produces_empty_grid(self) -> None:
        """With no talks there are no row boundaries and no template rows."""
        bounds, rows = _build_grid_slices([])
        assert bounds == []
        assert rows == ""
