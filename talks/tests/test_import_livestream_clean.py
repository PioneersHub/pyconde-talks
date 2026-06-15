"""Unit tests for the livestream-import dataframe cleaning step."""

from io import StringIO

import pandas as pd

from talks.management.commands.import_livestream_urls import (
    COL_EMBED_LINK,
    COL_END_TIME,
    COL_ROOM,
    COL_START_TIME,
    COL_VIMEO_RESTREAM,
    Command,
)


def _command() -> Command:
    """Return a Command instance with captured stdout/stderr."""
    cmd = Command()
    cmd.stdout = StringIO()  # type: ignore[assignment]
    cmd.stderr = StringIO()  # type: ignore[assignment]
    return cmd


def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build a raw spreadsheet-shaped frame from row dicts."""
    return pd.DataFrame(
        rows,
        columns=[COL_ROOM, COL_START_TIME, COL_END_TIME, COL_EMBED_LINK, COL_VIMEO_RESTREAM],
    )


def test_drops_rows_with_blank_times() -> None:
    """A row with a blank start or end time is dropped instead of aborting the import."""
    cmd = _command()
    df = _frame(
        [
            {
                COL_ROOM: "Room A",
                COL_START_TIME: "2026-06-12 10:00",
                COL_END_TIME: "2026-06-12 11:00",
                COL_EMBED_LINK: "https://vimeo.com/1",
                COL_VIMEO_RESTREAM: "Vimeo",
            },
            {
                COL_ROOM: "Room B",
                COL_START_TIME: None,  # missing start time
                COL_END_TIME: "2026-06-12 12:00",
                COL_EMBED_LINK: "https://vimeo.com/2",
                COL_VIMEO_RESTREAM: "Vimeo",
            },
        ],
    )

    cleaned = cmd._clean_streams_dataframe(df)

    assert len(cleaned) == 1
    assert cleaned.iloc[0][COL_ROOM] == "Room A"
    assert cleaned[COL_START_TIME].notna().all()
    assert "Skipped 1 row" in cmd.stdout.getvalue()  # type: ignore[union-attr]


def test_keeps_only_vimeo_rows_with_embed_link() -> None:
    """Non-Vimeo rows and rows without an embed link are filtered out."""
    cmd = _command()
    df = _frame(
        [
            {
                COL_ROOM: "Room A",
                COL_START_TIME: "2026-06-12 10:00",
                COL_END_TIME: "2026-06-12 11:00",
                COL_EMBED_LINK: "https://vimeo.com/1",
                COL_VIMEO_RESTREAM: "Vimeo",
            },
            {
                COL_ROOM: "Room B",
                COL_START_TIME: "2026-06-12 10:00",
                COL_END_TIME: "2026-06-12 11:00",
                COL_EMBED_LINK: None,  # no embed link
                COL_VIMEO_RESTREAM: "Vimeo",
            },
            {
                COL_ROOM: "Room C",
                COL_START_TIME: "2026-06-12 10:00",
                COL_END_TIME: "2026-06-12 11:00",
                COL_EMBED_LINK: "https://restream.io/3",
                COL_VIMEO_RESTREAM: "Restream",  # not Vimeo
            },
        ],
    )

    cleaned = cmd._clean_streams_dataframe(df)

    assert list(cleaned[COL_ROOM]) == ["Room A"]
