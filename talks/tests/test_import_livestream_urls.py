"""
Integration test for the import livestream URLs management command.

This test performs a real network call to Google Sheets when enabled via environment variable. It
verifies the command completes without failure.
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
def test_import_livestream_urls() -> None:
    """Run the command and check basic success criteria."""
    livestreams_sheet_id = getattr(settings, "LIVESTREAMS_SHEET_ID", None)
    livestreams_worksheet_name = getattr(settings, "LIVESTREAMS_WORKSHEET_NAME", None)

    if not (livestreams_sheet_id and livestreams_worksheet_name):
        pytest.skip(
            (
                "Configure LIVESTREAMS_SHEET_ID and LIVESTREAMS_WORKSHEET_NAME in settings to run "
                "this test."
            ),
        )

    stdout = StringIO()
    stderr = StringIO()

    call_command(
        "import_livestream_urls",
        f"--livestreams-worksheet-name={livestreams_worksheet_name}",
        f"--livestreams-sheet-id={livestreams_sheet_id}",
        verbosity=2,
        stdout=stdout,
        stderr=stderr,
    )

    # The command prints an error and returns early on failure
    assert "Command failed" not in stderr.getvalue()
