"""
Tests for the phone-first chrome: the bottom tab bar and the two-shape pages.

Layout is CSS and mostly out of reach here, so these assert the parts a template can get wrong:
which tabs a given visitor is offered, which one is marked current, that the collapsible filter
block does not swallow the search field, and that the schedule's agenda and grid renderings do not
collide on element ids.
"""

import re
from datetime import timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import ResolverMatch, reverse
from django.utils import timezone
from model_bakery import baker

from events.models import Event
from talks.models import Room, Talk
from talks.templatetags.nav_tags import NavItem, build_nav_items
from users.models import CustomUser


if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.test import Client


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def public_event() -> Event:
    """Return an event a logged-out visitor may browse."""
    return Event.objects.create(
        name="Public Event",
        slug="public",
        visibility=Event.Visibility.PUBLIC,
    )


@pytest.fixture
def member(public_event: Event) -> CustomUser:
    """Return a user holding a ticket for the public event."""
    user = baker.make(CustomUser, email="attendee@example.com")
    user.events.add(public_event)
    return user


def _scheduled_talk(event: Event, *, title: str = "Async all the way") -> Talk:
    """Return a talk in a room, today, so the schedule has something to lay out."""
    return baker.make(
        Talk,
        event=event,
        title=title,
        room=baker.make(Room, name="Room A"),
        start_time=timezone.now().replace(hour=10, minute=0, second=0, microsecond=0),
        duration=timedelta(minutes=30),
    )


def _request(url_name: str, path: str = "/", **params: str) -> HttpRequest:
    """
    Build a request that looks like it came out of the given view, from a logged-out visitor.

    ``build_nav_items`` reads ``resolver_match`` to decide which tab is current, and
    ``RequestFactory`` does not run URL resolution, so the match is attached by hand. Tests that
    need a signed-in visitor overwrite ``request.user``.
    """
    request = RequestFactory().get(path, params)
    request.resolver_match = ResolverMatch(func=lambda: None, args=(), kwargs={}, url_name=url_name)
    request.user = AnonymousUser()
    return request


def _tab_labels(items: list[NavItem]) -> list[str]:
    """Return the labels of a tab list, in order."""
    return [item.label for item in items]


def _active_label(items: list[NavItem]) -> str | None:
    """Return the label of the tab marked current, or None."""
    return next((item.label for item in items if item.is_active), None)


def _duplicate_ids(body: str) -> list[str]:
    """Return every id attribute value that appears more than once in the markup."""
    ids = re.findall(r'\bid="([^"]+)"', body)
    return sorted({value for value in ids if ids.count(value) > 1})


# ---------------------------------------------------------------------------
# Which tabs exist
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTabList:
    """The tab set depends on the visitor, for the same reasons the header links do."""

    def test_anonymous_visitor_is_offered_browsing_and_sign_in(self) -> None:
        """With a public event there is something to browse, and no account to have saved to."""
        request = _request("home")

        labels = _tab_labels(build_nav_items(request, has_public_event=True))

        assert labels == ["Home", "Talks", "Schedule", "Sign in"]

    def test_anonymous_visitor_without_a_public_event_gets_no_dead_links(self) -> None:
        """Both browsing tabs would lead to an empty page, so neither is offered."""
        request = _request("home")

        labels = _tab_labels(build_nav_items(request, has_public_event=False))

        assert labels == ["Home", "Sign in"]

    def test_member_gets_saved_and_profile(self, member: CustomUser) -> None:
        """An account makes Saved filterable server-side and Profile reachable."""
        request = _request("home")
        request.user = member

        labels = _tab_labels(build_nav_items(request, has_public_event=False))

        assert labels == ["Home", "Talks", "Schedule", "Saved", "Profile"]

    def test_saved_tab_points_at_the_saved_filter(self, member: CustomUser) -> None:
        """Saved is the talk list with its own filter, not a page of its own."""
        request = _request("home")
        request.user = member

        saved = next(
            item
            for item in build_nav_items(request, has_public_event=False)
            if item.label == "Saved"
        )

        assert saved.url == f"{reverse('talk_list')}?saved=1"


# ---------------------------------------------------------------------------
# Which tab is current
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestCurrentTab:
    """Marking the current tab is what makes a tab bar readable at a glance."""

    @pytest.mark.parametrize(
        ("url_name", "expected"),
        [
            ("home", "Home"),
            ("talk_list", "Talks"),
            ("talk_detail", "Talks"),
            ("talk_questions", "Talks"),
            ("schedule", "Schedule"),
            ("user_profile", "Profile"),
            ("socialaccount_connections", "Profile"),
        ],
    )
    def test_view_marks_its_own_tab(
        self,
        member: CustomUser,
        url_name: str,
        expected: str,
    ) -> None:
        """Drilling into a talk keeps the tab it was opened from marked."""
        request = _request(url_name)
        request.user = member

        assert _active_label(build_nav_items(request, has_public_event=True)) == expected

    def test_saved_filter_marks_saved_and_not_talks(self, member: CustomUser) -> None:
        """The two tabs share a view, so the filter is what tells them apart."""
        request = _request("talk_list", saved="1")
        request.user = member

        assert _active_label(build_nav_items(request, has_public_event=True)) == "Saved"

    def test_no_url_match_marks_nothing(self, member: CustomUser) -> None:
        """An error page renders the bar without claiming to be one of the tabs."""
        request = RequestFactory().get("/nope/")
        request.user = member

        assert _active_label(build_nav_items(request, has_public_event=True)) is None


# ---------------------------------------------------------------------------
# The bar as rendered
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestRenderedTabBar:
    """End to end, because the tag, the partial and base.html all have to line up."""

    def test_every_page_carries_the_bar(self, client: Client, public_event: Event) -> None:
        """It is in base.html, so a page that forgets to include anything still gets it."""
        response = client.get(reverse("home"))

        assert response.status_code == HTTPStatus.OK
        assert 'aria-label="Main sections"' in response.content.decode()

    def test_talks_page_marks_the_talks_tab(self, client: Client, public_event: Event) -> None:
        """The marker is ``aria-current``, which is also the stylesheet's hook."""
        body = client.get(reverse("talk_list")).content.decode()

        # The marked link is the one pointing at the talk list, inside the tab bar.
        assert re.search(r'href="/talks/"\s+class="bottom-nav-item"\s+aria-current="page"', body)

    def test_saved_tab_is_hidden_from_anonymous_visitors(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """Their bookmarks are in localStorage, where the server cannot filter on them."""
        body = client.get(reverse("home")).content.decode()

        assert "?saved=1" not in body
        assert "Sign in" in body

    def test_member_sees_the_saved_tab(self, client: Client, member: CustomUser) -> None:
        """Signed in, the filter has rows to match."""
        client.force_login(member)

        body = client.get(reverse("home")).content.decode()

        assert "?saved=1" in body


# ---------------------------------------------------------------------------
# Talk list: search out, filters in
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTalkListLayout:
    """The filter block collapses on a phone, so what it holds matters."""

    def test_search_stays_outside_the_collapsible_block(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """It is the control worth a permanent place on a small screen."""
        body = client.get(reverse("talk_list")).content.decode()

        assert body.index('name="q"') < body.index("data-filters")

    def test_filters_start_closed(self, client: Client, public_event: Event) -> None:
        """A phone must not paint the long version and then reflow; the script opens it above md."""
        body = client.get(reverse("talk_list")).content.decode()

        assert '<details class="filter-panel mb-4" data-filters>' in body

    def test_no_filters_means_no_badge(self, client: Client, public_event: Event) -> None:
        """Nothing is set, so the closed panel has nothing to announce."""
        response = client.get(reverse("talk_list"))

        assert response.context["filters_active"] is False

    @pytest.mark.parametrize("params", [{"status": "upcoming"}, {"saved": "1"}])
    def test_an_applied_filter_is_announced(
        self,
        client: Client,
        public_event: Event,
        params: dict[str, str],
    ) -> None:
        """A closed panel that is quietly filtering would look like a short list."""
        response = client.get(reverse("talk_list"), params)

        assert response.context["filters_active"] is True

    def test_a_stale_filter_value_does_not_count_as_active(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """The view blanks selections that do not match the event; the badge follows."""
        response = client.get(reverse("talk_list"), {"room": "123456"})

        assert response.context["filters_active"] is False


# ---------------------------------------------------------------------------
# Schedule: agenda below md, grid above
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestScheduleShapes:
    """One day, two renderings, no sideways scrolling on a phone."""

    def test_both_renderings_are_present(self, client: Client, public_event: Event) -> None:
        """CSS picks between them, so both have to be in the response."""
        talk = _scheduled_talk(public_event)

        body = client.get(reverse("schedule")).content.decode()

        assert "agenda-card" in body, "the phone agenda is missing"
        assert "schedule-grid" in body, "the desktop grid is missing"
        assert body.count(talk.title) >= 2  # noqa: PLR2004

    def test_each_rendering_carries_the_class_that_shows_or_hides_it(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """
        The stylesheet keys on these two classes and on the wrapper's data-view.

        Which rendering wins is a specificity question between a media query and an attribute
        selector, so the names have to match what input.css declares.
        """
        _scheduled_talk(public_event)

        body = client.get(reverse("schedule")).content.decode()

        assert 'class="schedule-layout"' in body
        assert 'class="schedule-agenda"' in body
        assert 'class="schedule-grid-wrap overflow-x-auto pb-4"' in body

    def test_the_agenda_groups_talks_by_start_time(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """A slot heading per start time is what replaces the grid's time column."""
        _scheduled_talk(public_event)

        body = client.get(reverse("schedule")).content.decode()

        assert 'class="agenda-slot"' in body

    def test_the_two_bookmark_buttons_have_distinct_wrappers(
        self,
        client: Client,
        member: CustomUser,
        public_event: Event,
    ) -> None:
        """
        Same id twice would send both buttons' HTMX swaps to whichever came first.

        So the agenda copy is prefixed, and the visible button is the one that updates.
        """
        client.force_login(member)
        talk = _scheduled_talk(public_event)

        body = client.get(reverse("schedule")).content.decode()

        assert f'id="sched-save-{talk.pk}"' in body
        assert f'id="sched-save-agenda-{talk.pk}"' in body
        assert f'hx-target="#sched-save-agenda-{talk.pk}"' in body
        assert not _duplicate_ids(body)


# ---------------------------------------------------------------------------
# Schedule: asking for the grid on a phone
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestScheduleLayoutChoice:
    """
    ?view=grid is how a phone visitor asks for the room-per-column grid anyway.

    The layout is otherwise the viewport's decision, so this parameter has to survive every link
    and form on the page: losing it would drop the visitor back to the agenda on the next tap.
    """

    def test_the_default_leaves_the_choice_to_the_viewport(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """An empty attribute is what lets the media query decide."""
        _scheduled_talk(public_event)

        body = client.get(reverse("schedule")).content.decode()

        assert 'data-view=""' in body

    def test_grid_is_requested_through_the_wrapper(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """One attribute, rather than a three-way conditional on both renderings."""
        _scheduled_talk(public_event)

        body = client.get(reverse("schedule"), {"view": "grid"}).content.decode()

        assert 'data-view="grid"' in body

    @pytest.mark.parametrize("value", ["", "agenda", "list", "GRID", "<script>"])
    def test_anything_else_falls_back_to_the_viewport(
        self,
        client: Client,
        public_event: Event,
        value: str,
    ) -> None:
        """Only ``grid`` is honoured, so junk in the URL cannot reach the attribute."""
        _scheduled_talk(public_event)

        response = client.get(reverse("schedule"), {"view": value})

        assert response.context["schedule_layout"] == "agenda"
        assert 'data-view=""' in response.content.decode()

    def test_the_switch_marks_the_layout_in_use(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """Both directions, since the pair is the only way to get back."""
        _scheduled_talk(public_event)

        default = client.get(reverse("schedule")).content.decode()
        grid = client.get(reverse("schedule"), {"view": "grid"}).content.decode()

        # The agenda link drops the parameter; the grid link sets it.
        assert re.search(r'href="\?"\s+class="segmented-item"\s+aria-current="page"', default)
        assert re.search(
            r'href="\?view=grid"\s+class="segmented-item"\s+aria-current="page"',
            grid,
        )

    def test_filtering_keeps_the_grid(self, client: Client, public_event: Event) -> None:
        """The filter form rebuilds the query string from its own fields, so it has to carry it."""
        _scheduled_talk(public_event)

        body = client.get(reverse("schedule"), {"view": "grid"}).content.decode()

        assert '<input type="hidden" name="view" value="grid" />' in body

    def test_picking_another_day_keeps_the_grid(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """The day pills are links, and querystring preserves everything it is not given."""
        talk = _scheduled_talk(public_event)
        day = talk.start_time.date().isoformat()

        body = client.get(reverse("schedule"), {"view": "grid"}).content.decode()

        assert f'href="?view=grid&amp;date={day}"' in body

    def test_clearing_filters_keeps_the_grid(
        self,
        client: Client,
        public_event: Event,
    ) -> None:
        """Clear all drops the four filters, not the day, the event or the layout."""
        _scheduled_talk(public_event, title="Findable")

        body = client.get(
            reverse("schedule"),
            {"view": "grid", "q": "Findable"},
        ).content.decode()

        match = re.search(r'href="([^"]+)"\s+class="btn-neutral[^"]*">\s*Clear all', body)

        assert match, "the Clear all link is missing"
        assert "view=grid" in match.group(1)
        assert "q=" not in match.group(1)


# ---------------------------------------------------------------------------
# Ids stay unique
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestNoDuplicateIds:
    """
    Duplicate ids are how a twin-layout page breaks.

    HTMX targets and label ``for`` attributes both resolve to the first match, so a second element
    carrying the same id silently steals or misses updates.
    """

    def test_pages_have_unique_ids(
        self,
        client: Client,
        member: CustomUser,
        public_event: Event,
    ) -> None:
        """Every page an attendee reaches from the tab bar."""
        client.force_login(member)
        talk = _scheduled_talk(public_event)

        for url in (
            reverse("home"),
            reverse("talk_list"),
            reverse("schedule"),
            reverse("talk_detail", args=[talk.pk]),
            reverse("user_profile"),
        ):
            body = client.get(url).content.decode()
            assert not _duplicate_ids(body), f"{url} renders duplicate ids"
