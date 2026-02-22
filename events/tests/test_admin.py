"""Tests for the Event admin interface."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.admin import AdminSite
from django.urls import reverse
from model_bakery import baker

from events.admin import EventAdmin
from events.models import Event
from users.models import CustomUser


if TYPE_CHECKING:
    from django.test.client import Client


@pytest.fixture()
def superuser() -> CustomUser:
    """Create a superuser for admin access."""
    return CustomUser.objects.create_superuser(
        email="admin@example.com",
        password="password",
    )


@pytest.mark.django_db
class TestEventAdmin:
    """Tests for the EventAdmin configuration and views."""

    def test_list_display_fields(self) -> None:
        """Verify list_display fields are correctly configured."""
        expected = (
            "name",
            "slug",
            "year",
            "is_active",
            "validation_api_url_set",
        )
        assert EventAdmin.list_display == expected

    def test_search_fields(self) -> None:
        """Verify search_fields are correctly configured."""
        assert EventAdmin.search_fields == ("name", "slug")

    def test_prepopulated_fields(self) -> None:
        """Verify slug is prepopulated from name."""
        assert EventAdmin.prepopulated_fields == {"slug": ("name",)}

    def test_validation_api_url_set_true(self) -> None:
        """validation_api_url_set returns True when URL is configured."""
        event = baker.make(Event, validation_api_url="https://example.com/api")
        admin_instance = EventAdmin(Event, AdminSite())
        assert admin_instance.validation_api_url_set(event) is True

    def test_validation_api_url_set_false(self) -> None:
        """validation_api_url_set returns False when URL is empty."""
        event = baker.make(Event, validation_api_url="")
        admin_instance = EventAdmin(Event, AdminSite())
        assert admin_instance.validation_api_url_set(event) is False

    def test_changelist_view(self, client: Client, superuser: CustomUser) -> None:
        """Verify the event changelist page loads successfully."""
        client.force_login(superuser)
        baker.make(Event, name="Test Event", slug="test-event", year=2025)
        url = reverse("admin:events_event_changelist")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Test Event" in response.content.decode()

    def test_add_view(self, client: Client, superuser: CustomUser) -> None:
        """Verify the event add page loads successfully."""
        client.force_login(superuser)
        url = reverse("admin:events_event_add")
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_add_event_post(self, client: Client, superuser: CustomUser) -> None:
        """Verify creating a new event via admin POST works."""
        client.force_login(superuser)
        url = reverse("admin:events_event_add")
        response = client.post(
            url,
            {
                "name": "New Event 2026",
                "slug": "new-event-2026",
                "year": 2026,
                "validation_api_url": "",
                "is_active": True,
            },
        )
        assert response.status_code == HTTPStatus.FOUND
        assert Event.objects.filter(slug="new-event-2026").exists()

    def test_change_view(self, client: Client, superuser: CustomUser) -> None:
        """Verify the event change page loads successfully."""
        client.force_login(superuser)
        event = baker.make(Event, name="Edit Me", slug="edit-me", year=2025)
        url = reverse("admin:events_event_change", args=[event.pk])
        response = client.get(url)
        assert response.status_code == HTTPStatus.OK
        assert "Edit Me" in response.content.decode()
