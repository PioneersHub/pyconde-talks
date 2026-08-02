"""Tests for project-level URL configuration."""

from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from django.conf import settings
from django.urls import reverse


if TYPE_CHECKING:
    from django.test import Client
    from pytest_mock import MockerFixture


def test_admin_mounted_at_configured_url() -> None:
    """The admin is mounted at settings.ADMIN_URL, not a hardcoded '/admin/'."""
    assert reverse("admin:index") == "/" + settings.ADMIN_URL


@pytest.mark.django_db
def test_health_endpoint_excludes_mail_check(client: Client, mocker: MockerFixture) -> None:
    """
    The public liveness probe must not run the Mail check.

    The Mail check opens a real SMTP/Mailgun connection on every hit; on this unauthenticated,
    constantly-probed endpoint that is both an outbound-connection amplifier and a source of false
    "unhealthy" status (and deploy rollbacks) during ESP outages.
    """
    # Pin the psutil-backed Disk and Memory probes to healthy readings. Both warn past 90 % usage,
    # and one warning is enough to make the endpoint answer 500, so leaving them live would turn
    # this into a test of whichever machine runs it: it fails on a laptop with a full disk, for
    # reasons that have nothing to do with the Mail check.
    mocker.patch("psutil.disk_usage", return_value=SimpleNamespace(percent=42.0))
    mocker.patch(
        "psutil.virtual_memory",
        return_value=SimpleNamespace(available=8 * 1024**3, total=16 * 1024**3, percent=50.0),
    )

    response = client.get("/ht/?format=json")

    assert response.status_code == HTTPStatus.OK
    body = response.content.decode()
    assert "Mail" not in body
    # Guard the assertion above against a refactor that empties the check list: "Mail" would then
    # be absent for the wrong reason.
    assert "Database" in body
