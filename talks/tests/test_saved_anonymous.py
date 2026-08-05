"""
Tests for bookmarks made while logged out, and the merge that folds them into an account.

The client half lives in ``static/js/saved-talks.js`` and has no test runner in this project, so the
server side is covered thoroughly and the markup contract the script depends on is asserted here
too: if a data attribute is renamed, these fail rather than the feature quietly breaking in the
browser.
"""

import json
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.test import Client
from django.urls import reverse
from model_bakery import baker

from events.models import Event
from talks.models import SavedTalk, Talk
from talks.views_saved import MAX_MERGE_IDS
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse


@pytest.fixture
def event() -> Event:
    """Return a public event, so anonymous visitors can browse its talks."""
    return Event.objects.create(
        name="Public Event",
        slug="public-event",
        visibility=Event.Visibility.PUBLIC,
    )


@pytest.fixture
def talks(event: Event) -> list[Talk]:
    """Return three talks on the public event."""
    return [baker.make(Talk, event=event, title=f"Talk {i}") for i in range(3)]


@pytest.fixture
def member(event: Event) -> CustomUser:
    """Return a user with access to the event."""
    user = baker.make(CustomUser, email="member@example.com")
    user.events.add(event)
    return user


def _merge(client: Client, ids: object) -> _MonkeyPatchedWSGIResponse:
    """POST a merge payload as the browser does."""
    return client.post(
        reverse("merge_saved_talks"),
        data=json.dumps({"ids": ids}),
        content_type="application/json",
    )


@pytest.mark.django_db
class TestAnonymousSaveButton:
    """The markup contract the client script relies on."""

    def test_the_button_is_offered_to_anonymous_visitors(
        self,
        client: Client,
        talks: list[Talk],
    ) -> None:
        """
        Logged-out visitors get a bookmark button, not a missing one.

        Building a personal schedule before deciding to sign in is the point of the feature.
        """
        response = client.get(reverse("talk_detail", kwargs={"pk": talks[0].pk}))
        body = response.content.decode()

        assert f'data-save-talk="{talks[0].pk}"' in body
        assert f'data-save-toggle="{talks[0].pk}"' in body

    def test_the_anonymous_button_does_not_post_to_the_server(
        self,
        client: Client,
        talks: list[Talk],
    ) -> None:
        """There is no row to toggle, so the button must not try; the script handles it."""
        response = client.get(reverse("talk_detail", kwargs={"pk": talks[0].pk}))
        body = response.content.decode()

        assert reverse("toggle_save_talk", kwargs={"talk_id": talks[0].pk}) not in body

    def test_the_signed_in_button_still_posts(
        self,
        client: Client,
        talks: list[Talk],
        member: CustomUser,
    ) -> None:
        """The existing server-side path is unchanged for signed-in users."""
        client.force_login(member)
        response = client.get(reverse("talk_detail", kwargs={"pk": talks[0].pk}))
        body = response.content.decode()

        assert reverse("toggle_save_talk", kwargs={"talk_id": talks[0].pk}) in body
        assert f'data-save-toggle="{talks[0].pk}"' not in body

    def test_translated_labels_travel_in_data_attributes(
        self,
        client: Client,
        talks: list[Talk],
    ) -> None:
        """
        The script must never hardcode English.

        Labels and titles are rendered through gettext and handed to JavaScript as data attributes,
        so switching language switches the button text too.
        """
        response = client.get(reverse("talk_detail", kwargs={"pk": talks[0].pk}))
        body = response.content.decode()

        assert "data-title-saved=" in body
        assert "data-title-unsaved=" in body
        assert "data-label-saved=" in body
        assert "data-label-unsaved=" in body

    def test_the_page_carries_what_the_script_needs(
        self,
        client: Client,
        talks: list[Talk],
    ) -> None:
        """The merge URL and the CSRF token reach the script through the document."""
        response = client.get(reverse("talk_detail", kwargs={"pk": talks[0].pk}))
        body = response.content.decode()

        assert reverse("merge_saved_talks") in body
        assert 'id="csrf-form"' in body
        assert "csrfmiddlewaretoken" in body


@pytest.mark.django_db
class TestMergeEndpoint:
    """Folding local bookmarks into the account."""

    def test_requires_a_login(self, client: Client) -> None:
        """There is no account to merge into otherwise."""
        response = _merge(client, [1])
        assert response.status_code == HTTPStatus.FOUND
        assert "/accounts/login/" in response.headers.get("Location", "")

    def test_requires_post(self, client: Client, member: CustomUser) -> None:
        """A merge changes data, so GET is refused."""
        client.force_login(member)
        response = client.get(reverse("merge_saved_talks"))
        assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED

    def test_merges_accessible_talks(
        self,
        client: Client,
        talks: list[Talk],
        member: CustomUser,
    ) -> None:
        """The ordinary path: the browser's list becomes rows on the account."""
        client.force_login(member)
        response = _merge(client, [talks[0].pk, talks[1].pk])

        assert response.status_code == HTTPStatus.OK
        assert response.json()["merged"] == 2  # noqa: PLR2004
        assert SavedTalk.talk_ids_for(member) == {talks[0].pk, talks[1].pk}

    def test_is_idempotent(
        self,
        client: Client,
        talks: list[Talk],
        member: CustomUser,
    ) -> None:
        """
        Merging twice adds nothing the second time.

        The client only clears its copy on a 200, so a response that never arrived means the same
        payload is sent again on the next page view.
        """
        client.force_login(member)
        _merge(client, [talks[0].pk])
        response = _merge(client, [talks[0].pk])

        assert response.json()["merged"] == 0
        assert SavedTalk.objects.filter(user=member).count() == 1

    def test_existing_bookmarks_are_preserved(
        self,
        client: Client,
        talks: list[Talk],
        member: CustomUser,
    ) -> None:
        """A union, never a replacement: the account's own picks are not dropped."""
        SavedTalk.objects.create(user=member, talk=talks[2])
        client.force_login(member)

        _merge(client, [talks[0].pk])

        assert SavedTalk.talk_ids_for(member) == {talks[0].pk, talks[2].pk}

    def test_inaccessible_talks_are_silently_dropped(
        self,
        client: Client,
        talks: list[Talk],
        member: CustomUser,
    ) -> None:
        """
        A crafted payload cannot confirm that a hidden talk exists.

        The ids are scoped through ``accessible_to``, and unknown ones are dropped rather than
        reported, so the response is the same whether or not the id is real.
        """
        hidden_event = Event.objects.create(
            name="Hidden",
            slug="hidden",
            visibility=Event.Visibility.HIDDEN,
        )
        hidden_talk = baker.make(Talk, event=hidden_event, title="Secret")
        client.force_login(member)

        response = _merge(client, [talks[0].pk, hidden_talk.pk, 999999])

        assert response.status_code == HTTPStatus.OK
        assert SavedTalk.talk_ids_for(member) == {talks[0].pk}

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param("not-a-list", id="not-a-list"),
            pytest.param([1] * (MAX_MERGE_IDS + 1), id="over-the-cap"),
        ],
    )
    def test_bad_payloads_are_refused(
        self,
        client: Client,
        member: CustomUser,
        payload: object,
    ) -> None:
        """Malformed or oversized input is a 400, not a 500."""
        client.force_login(member)
        response = _merge(client, payload)
        assert response.status_code == HTTPStatus.BAD_REQUEST

    def test_malformed_json_is_refused(self, client: Client, member: CustomUser) -> None:
        """A body that is not JSON at all is handled the same way."""
        client.force_login(member)
        response = client.post(
            reverse("merge_saved_talks"),
            data="{not json",
            content_type="application/json",
        )
        assert response.status_code == HTTPStatus.BAD_REQUEST

    def test_non_integer_ids_are_ignored(
        self,
        client: Client,
        talks: list[Talk],
        member: CustomUser,
    ) -> None:
        """Strings, nulls and booleans in the list are dropped rather than crashing a query."""
        client.force_login(member)
        response = _merge(client, [talks[0].pk, "abc", None, True, -5, 0])

        assert response.status_code == HTTPStatus.OK
        assert SavedTalk.talk_ids_for(member) == {talks[0].pk}

    def test_csrf_is_enforced(self, talks: list[Talk], member: CustomUser) -> None:
        """The endpoint writes, so it is not exempt from CSRF."""
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(member)

        response = csrf_client.post(
            reverse("merge_saved_talks"),
            data=json.dumps({"ids": [talks[0].pk]}),
            content_type="application/json",
        )
        assert response.status_code == HTTPStatus.FORBIDDEN
