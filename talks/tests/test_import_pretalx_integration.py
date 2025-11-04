"""
Integration test for the Pretalx import management command.

This test performs a real network call to Pretalx when enabled via environment variable. It verifies
the command completes without a fetch failure and, if any talks are created, that their Pretalx
links are built from the provided base URL.
"""

import os
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command

from talks.models import Talk


RUN_LIVE = os.getenv("RUN_LIVE_IMPORT_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="Set RUN_LIVE_IMPORT_TEST=1 to run live Pretalx import integration test.",
)


@pytest.mark.django_db
def test_import_pretalx_live_fetch_and_link_construction() -> None:
    """Run the command against a live Pretalx endpoint and check basic success criteria."""
    pretalx_base_url = getattr(settings, "PRETALX_BASE_URL", None)
    pretalx_event_slug = getattr(settings, "PRETALX_EVENT_SLUG", None)
    pretalx_api_token = getattr(settings, "PRETALX_API_TOKEN", None)

    if not (pretalx_base_url and pretalx_event_slug and pretalx_api_token):
        pytest.skip(
            (
                "Configure PRETALX_BASE_URL, PRETALX_EVENT_SLUG, and PRETALX_API_TOKEN in settings "
                "to run this test."
            ),
        )

    stdout = StringIO()
    stderr = StringIO()

    call_command(
        "import_pretalx_talks",
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
        expected_prefix = f"{pretalx_base_url.rstrip('/')}/{pretalx_event_slug}/talk/"
        for talk in talks:
            assert talk.pretalx_link.startswith(expected_prefix)
