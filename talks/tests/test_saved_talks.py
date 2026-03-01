"""Tests for the SavedTalk model, views, and template filter."""

# ruff: noqa: PLR2004

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError
from django.template import Context, Template
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from talks.models import SavedTalk, Talk
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def user() -> CustomUser:
    """Create a regular user for testing."""
    return baker.make(CustomUser, email="saver@example.com")


@pytest.fixture()
def other_user() -> CustomUser:
    """Create another user for testing."""
    return baker.make(CustomUser, email="other@example.com")


@pytest.fixture()
def talk() -> Talk:
    """Create a talk for testing."""
    return baker.make(Talk, title="Saved Test Talk", start_time=timezone.now())


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSavedTalkModel:
    """Tests for the SavedTalk model."""

    def test_create_saved_talk(self, user: CustomUser, talk: Talk) -> None:
        """Create a saved talk with valid data."""
        saved = SavedTalk.objects.create(user=user, talk=talk)
        assert saved.user == user
        assert saved.talk == talk
        assert saved.created_at is not None

    def test_str_representation(self, user: CustomUser, talk: Talk) -> None:
        """Verify the string representation of a saved talk."""
        saved = SavedTalk.objects.create(user=user, talk=talk)
        assert str(user) in str(saved)
        assert "saved" in str(saved)

    def test_unique_constraint(self, user: CustomUser, talk: Talk) -> None:
        """A user cannot save the same talk twice."""
        SavedTalk.objects.create(user=user, talk=talk)

        with pytest.raises(IntegrityError):
            SavedTalk.objects.create(user=user, talk=talk)

    def test_different_users_can_save_same_talk(
        self,
        user: CustomUser,
        other_user: CustomUser,
        talk: Talk,
    ) -> None:
        """Different users can save the same talk."""
        SavedTalk.objects.create(user=user, talk=talk)
        SavedTalk.objects.create(user=other_user, talk=talk)
        assert SavedTalk.objects.filter(talk=talk).count() == 2

    def test_cascade_delete_user(self, user: CustomUser, talk: Talk) -> None:
        """Deleting a user cascades to their saved talks."""
        SavedTalk.objects.create(user=user, talk=talk)
        user.delete()
        assert SavedTalk.objects.count() == 0

    def test_cascade_delete_talk(self, user: CustomUser, talk: Talk) -> None:
        """Deleting a talk cascades to saved entries."""
        SavedTalk.objects.create(user=user, talk=talk)
        talk.delete()
        assert SavedTalk.objects.count() == 0


# ---------------------------------------------------------------------------
# View Tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestToggleSaveTalkView:
    """Tests for the toggle_save_talk view."""

    def test_save_talk(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """POST to toggle_save_talk creates a SavedTalk."""
        client.force_login(user)
        url = reverse("toggle_save_talk", args=[talk.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        assert SavedTalk.objects.filter(user=user, talk=talk).exists()

    def test_unsave_talk(self, client: Client, user: CustomUser, talk: Talk) -> None:
        """POST to toggle_save_talk removes an existing SavedTalk."""
        client.force_login(user)
        SavedTalk.objects.create(user=user, talk=talk)
        url = reverse("toggle_save_talk", args=[talk.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        assert not SavedTalk.objects.filter(user=user, talk=talk).exists()

    def test_htmx_save_returns_partial(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """HTMX request returns a partial HTML response."""
        client.force_login(user)
        url = reverse("toggle_save_talk", args=[talk.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        assert b"save-btn-" in response.content

    def test_htmx_unsave_returns_partial(
        self,
        client: Client,
        user: CustomUser,
        talk: Talk,
    ) -> None:
        """HTMX unsave request returns partial with outline bookmark."""
        client.force_login(user)
        SavedTalk.objects.create(user=user, talk=talk)
        url = reverse("toggle_save_talk", args=[talk.pk])
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Save" in content

    def test_requires_login(self, client: Client, talk: Talk) -> None:
        """Unauthenticated users are redirected to login."""
        url = reverse("toggle_save_talk", args=[talk.pk])
        response = client.post(url)
        assert response.status_code == HTTPStatus.FOUND
        assert "/accounts/login/" in response.url  # type: ignore[attr-defined]

    def test_nonexistent_talk_returns_404(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """POST for a nonexistent talk returns 404."""
        client.force_login(user)
        url = reverse("toggle_save_talk", args=[99999])
        response = client.post(url)
        assert response.status_code == HTTPStatus.NOT_FOUND


# ---------------------------------------------------------------------------
# Filter Tests (saved filter in talk list)
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestSavedTalkFilter:
    """Tests for filtering talks by saved status in the list view."""

    def test_filter_saved_shows_only_saved_talks(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """Filtering by saved=1 shows only the user's saved talks."""
        client.force_login(user)
        talk1 = baker.make(Talk, title="Saved Talk", start_time=timezone.now())
        baker.make(Talk, title="Unsaved Talk", start_time=timezone.now())
        SavedTalk.objects.create(user=user, talk=talk1)

        url = reverse("talk_list") + "?saved=1"
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        content = response.content.decode()
        assert "Saved Talk" in content
        assert "Unsaved Talk" not in content

    def test_no_filter_shows_all_talks(
        self,
        client: Client,
        user: CustomUser,
    ) -> None:
        """Without saved filter, all talks are shown."""
        client.force_login(user)
        baker.make(Talk, title="Talk A", start_time=timezone.now())
        baker.make(Talk, title="Talk B", start_time=timezone.now())

        url = reverse("talk_list")
        response = client.get(url)
        content = response.content.decode()
        assert "Talk A" in content
        assert "Talk B" in content


# ---------------------------------------------------------------------------
# Template Filter Tests
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestIsInFilter:
    """Tests for the is_in template filter."""

    def test_value_in_set(self) -> None:
        """Returns True when value is in the set."""
        template = Template("{% load saved_tags %}{{ value|is_in:container }}")
        context = Context({"value": 1, "container": {1, 2, 3}})
        assert template.render(context) == "True"

    def test_value_not_in_set(self) -> None:
        """Returns False when value is not in the set."""
        template = Template("{% load saved_tags %}{{ value|is_in:container }}")
        context = Context({"value": 5, "container": {1, 2, 3}})
        assert template.render(context) == "False"

    def test_empty_container(self) -> None:
        """Returns False for an empty container."""
        template = Template("{% load saved_tags %}{{ value|is_in:container }}")
        context = Context({"value": 1, "container": set()})
        assert template.render(context) == "False"
