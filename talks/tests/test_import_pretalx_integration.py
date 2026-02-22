"""
Integration test for the Pretalx import management command.

This test performs a real network call to Pretalx when enabled via environment variable. It verifies
the command completes without a fetch failure and, if any talks are created, that their Pretalx
links are built from the provided base URL.

Requires environment variables:
    TEST_PRETALX_EVENT_URL - e.g. https://pretalx.com/pyconde-pydata-2026
    PRETALX_API_TOKEN - a valid API token
"""

import os
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command

from events.models import Event
from talks.models import Talk


RUN_LIVE = os.getenv("RUN_LIVE_IMPORT_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="Set RUN_LIVE_IMPORT_TEST=1 to run live Pretalx import integration test.",
)


@pytest.mark.django_db
def test_import_pretalx_live_fetch_and_link_construction() -> None:
    """Run the command against a live Pretalx endpoint and check basic success criteria."""
    pretalx_api_token = getattr(settings, "PRETALX_API_TOKEN", "")
    pretalx_event_url = os.getenv("TEST_PRETALX_EVENT_URL", "")

    if not (pretalx_api_token and pretalx_event_url):
        pytest.skip("Set PRETALX_API_TOKEN and TEST_PRETALX_EVENT_URL to run this test.")

    # Create an Event so the command can resolve it
    Event.objects.get_or_create(
        slug="test-event",
        defaults={
            "name": "Test Event",
            "year": 2025,
            "pretalx_url": pretalx_event_url,
        },
    )

    stdout = StringIO()
    stderr = StringIO()

    call_command(
        "import_pretalx_talks",
        "--event=test-event",
        "--max-retries=1",
        verbosity=2,
        stdout=stdout,
        stderr=stderr,
    )

    # The command prints an error and returns early on fetch failure.
    assert "Failed to fetch talks:" not in stderr.getvalue()

    # If any talks were created, ensure their links use the provided base URL pattern.
    talks = Talk.objects.all()
    if talks.exists():
        expected_prefix = f"{pretalx_event_url.rstrip('/')}/talk/"
        for talk in talks:
            assert talk.pretalx_link.startswith(expected_prefix)
