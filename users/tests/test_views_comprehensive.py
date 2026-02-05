"""Tests for users.views covering CustomRequestLoginCodeView and profile_view."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.urls import reverse
from model_bakery import baker

from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.fixture()
def user() -> CustomUser:
    """Return a test user for profile view tests."""
    return baker.make(CustomUser, email="profile@example.com")


@pytest.mark.django_db
class TestProfileView:
    """Tests for profile_view."""

    def test_get_profile(self, client: Client, user: CustomUser) -> None:
        """Display the profile form for an authenticated user."""
        client.force_login(user)
        url = reverse("user_profile")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_post_profile_valid(self, client: Client, user: CustomUser) -> None:
        """Save the updated profile and redirect on valid POST data."""
        client.force_login(user)
        url = reverse("user_profile")
        response = client.post(
            url,
            {"first_name": "Updated", "last_name": "User", "display_name": "NewDisplay"},
        )
        assert response.status_code == HTTPStatus.FOUND
        user.refresh_from_db()
        assert user.display_name == "NewDisplay"

    def test_post_profile_invalid(self, client: Client, user: CustomUser) -> None:
        """Re-render the form with errors when POST data is invalid."""
        client.force_login(user)
        url = reverse("user_profile")
        # display_name has max_length=100, exceed it
        response = client.post(
            url,
            {"first_name": "A", "last_name": "B", "display_name": "x" * 200},
        )
        assert response.status_code == HTTPStatus.OK  # re-renders form

    def test_unauthenticated_redirect(self, client: Client) -> None:
        """Redirect unauthenticated users to the login page."""
        url = reverse("user_profile")
        response = client.get(url)
        assert response.status_code == HTTPStatus.FOUND
