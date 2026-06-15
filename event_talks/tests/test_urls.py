"""Tests for project-level URL configuration."""

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.urls import reverse


if TYPE_CHECKING:
    from django.test import Client


def test_admin_mounted_at_configured_url() -> None:
    """The admin is mounted at settings.ADMIN_URL, not a hardcoded '/admin/'."""
    assert reverse("admin:index") == "/" + settings.ADMIN_URL


@pytest.mark.django_db
def test_health_endpoint_excludes_mail_check(client: Client) -> None:
    """
    The public liveness probe must not run the Mail check.

    The Mail check opens a real SMTP/Mailgun connection on every hit; on this unauthenticated,
    constantly-probed endpoint that is both an outbound-connection amplifier and a source of
    false "unhealthy" status (and deploy rollbacks) during ESP outages.
    """
    response = client.get("/ht/?format=json")
    assert response.status_code == HTTPStatus.OK
    body = response.content.decode()
    assert "Mail" not in body
