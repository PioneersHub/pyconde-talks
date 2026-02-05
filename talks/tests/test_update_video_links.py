"""
Integration test for the update video links management command.

This test performs a real network call to Vimeo when enabled via environment variable.
It verifies the command completes without failure.
"""

import os
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command


RUN_LIVE = os.getenv("RUN_LIVE_IMPORT_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="Set RUN_LIVE_IMPORT_TEST=1 to run this integration test.",
)


@pytest.mark.django_db
def test_update_video_links() -> None:
    """Run the command and check basic success criteria."""
    vimeo_access_token = getattr(settings, "VIMEO_ACCESS_TOKEN", None)
    vimeo_project_ids = getattr(settings, "VIMEO_PROJECT_IDS", None)

    if not (vimeo_access_token and vimeo_project_ids):
        pytest.skip(
            ("Configure VIMEO_ACCESS_TOKEN and VIMEO_PROJECT_IDS in settings to run this test."),
        )

    stdout = StringIO()
    stderr = StringIO()

    call_command(
        "update_video_links",
        f"--vimeo-access-token={vimeo_access_token}",
        f"--vimeo-project-ids={vimeo_project_ids}",
        verbosity=2,
        stdout=stdout,
        stderr=stderr,
    )

    # The command prints an error and returns early on failure
    assert "Command failed" not in stderr.getvalue()
