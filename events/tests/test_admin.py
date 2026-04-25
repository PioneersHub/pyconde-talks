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
            "show_rating_summary",
            "validation_api_url_set",
        )
        assert EventAdmin.list_display == expected

    def test_search_fields(self) -> None:
        """Verify search_fields are correctly configured."""
        assert EventAdmin.search_fields == ("name", "slug")

    def test_prepopulated_fields(self) -> None:
        """Verify slug is prepopulated from name."""
        assert EventAdmin.prepopulated_fields == {"slug": ("name",)}

    def test_branding_fieldset_includes_legal_links(self) -> None:
        """Branding fieldset exposes legal link fields in admin."""
        branding_fieldset = next(
            fields["fields"] for name, fields in EventAdmin.fieldsets if name == "Branding"
        )
        assert "imprint_url" in branding_fieldset
        assert "code_of_conduct_url" in branding_fieldset
        assert "privacy_policy_url" in branding_fieldset

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

    def test_change_view_shows_users_widget(
        self,
        client: Client,
        superuser: CustomUser,
    ) -> None:
        """Event change page exposes a users multi-select populated with existing access."""
        client.force_login(superuser)
        event = baker.make(Event, name="Access Event", slug="access-event", year=2025)
        with_access = CustomUser.objects.create_user(email="in@example.com")
        CustomUser.objects.create_user(email="out@example.com")
        event.users.add(with_access)

        url = reverse("admin:events_event_change", args=[event.pk])
        response = client.get(url)
        content = response.content.decode()

        assert response.status_code == HTTPStatus.OK
        assert 'name="users"' in content
        assert f'value="{with_access.pk}" selected' in content

    def test_change_view_saves_users(self, client: Client, superuser: CustomUser) -> None:
        """Submitting the change form batch-updates which users have access."""
        client.force_login(superuser)
        event = baker.make(
            Event,
            name="Batch Event",
            slug="batch-event",
            year=2026,
            validation_api_url="",
        )
        keep = CustomUser.objects.create_user(email="keep@example.com")
        add = CustomUser.objects.create_user(email="add@example.com")
        drop = CustomUser.objects.create_user(email="drop@example.com")
        event.users.set([keep, drop])

        url = reverse("admin:events_event_change", args=[event.pk])
        response = client.post(
            url,
            {
                "name": event.name,
                "slug": event.slug,
                "year": event.year,
                "validation_api_url": "",
                "is_active": "on",
                "show_rating_summary": "on",
                "users": [str(keep.pk), str(add.pk)],
            },
        )

        assert response.status_code == HTTPStatus.FOUND
        assert set(event.users.values_list("pk", flat=True)) == {keep.pk, add.pk}
        assert drop.events.filter(pk=event.pk).count() == 0
