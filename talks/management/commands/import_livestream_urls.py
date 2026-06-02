"""Management command for filling the live streams from Google Sheets."""

import io
from typing import Any

import httpx
import pandas as pd
import structlog
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from events.models import Event
from talks.models import Room, Streaming


logger = structlog.get_logger(__name__)

COL_ROOM = "Room"
COL_START_TIME = "Start Time"
COL_END_TIME = "End Time"
COL_EMBED_LINK = "Embed Link"
COL_VIMEO_RESTREAM = "Vimeo / Restream"


class Command(BaseCommand):
    """Fetch streamings from Google Sheets and save them to the database."""

    help = "Import streamings from Google Sheets"

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command line arguments."""
        parser.add_argument(
            "--livestreams-sheet-id",
            type=str,
            default=settings.LIVESTREAMS_SHEET_ID,
            help="Google Sheets ID for the livestreams sheet",
        )
        parser.add_argument(
            "--livestreams-worksheet-name",
            type=str,
            default=settings.LIVESTREAMS_WORKSHEET_NAME,
            help="Name of the worksheet in the Google Sheets",
        )
        parser.add_argument(
            "--event-slug",
            type=str,
            default=getattr(settings, "DEFAULT_EVENT", ""),
            help="Scope rooms and replaced streamings to this event (default: DEFAULT_EVENT). "
            "Required when the same room name exists in more than one event.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Perform a dry run without making database changes",
        )

    def fetch_spreadsheet_data(self, sheet_id: str, worksheet_name: str) -> pd.DataFrame:
        """Fetch and process data from Google Sheets."""
        try:
            url = f"https://docs.google.com/spreadsheet/ccc?key={sheet_id}&output=xlsx"
            self.stdout.write("Fetching data from Google Sheets...")

            response = httpx.get(url, timeout=30, follow_redirects=True)
            response.raise_for_status()
            data = io.BytesIO(response.content)
            s_df = pd.read_excel(data, sheet_name=worksheet_name)
            s_df = s_df[(s_df[COL_VIMEO_RESTREAM] == "Vimeo") & (s_df[COL_EMBED_LINK].notna())]
            s_df = s_df[[COL_ROOM, COL_START_TIME, COL_END_TIME, COL_EMBED_LINK]]

            # Localize timestamps
            s_df[COL_START_TIME] = pd.to_datetime(s_df[COL_START_TIME]).dt.tz_localize(
                "Europe/Berlin",
            )
            s_df[COL_END_TIME] = pd.to_datetime(s_df[COL_END_TIME]).dt.tz_localize("Europe/Berlin")
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Error fetching spreadsheet data: {exc}"))
            raise
        else:
            return s_df

    def get_room(self, room_name: str, event: Event | None = None) -> Room | None:
        """
        Get a room by name, scoped to *event* when given.

        Rooms are event-scoped, so the same name can exist in several events. With an
        event, the lookup is unambiguous; without one it falls back to a global match
        (first by ordering) for backward compatibility when no event is configured.
        """
        room_name = room_name.strip()
        qs = Room.objects.filter(name=room_name)
        if event is not None:
            qs = qs.filter(event=event)
        return qs.first()

    def _import_streams(self, s_df: pd.DataFrame, event: Event | None = None) -> None:
        """Replace existing streams with the ones in the DataFrame, scoped to *event*."""
        # Only wipe the target event's streamings (or all of them when no event is set),
        # so importing one event's sheet can't clear another event's livestreams.
        existing = (
            Streaming.objects.all()
            if event is None
            else Streaming.objects.filter(room__event=event)
        )
        deleted_count = existing.delete()[0]
        self.stdout.write(
            self.style.WARNING(f"Deleted {deleted_count} existing streaming sessions"),
        )

        streams_to_create: list[Streaming] = []
        skipped_count = 0

        for _, row in s_df.iterrows():
            room = self.get_room(row[COL_ROOM], event)
            if not room:
                self.stdout.write(
                    self.style.WARNING(
                        f"Room '{row[COL_ROOM].strip()}' not found. Skipping this row.",
                    ),
                )
                skipped_count += 1
                continue
            streams_to_create.append(
                Streaming(
                    room=room,
                    start_time=row[COL_START_TIME],
                    end_time=row[COL_END_TIME],
                    video_link=row[COL_EMBED_LINK],
                ),
            )

        if streams_to_create:
            Streaming.objects.bulk_create(streams_to_create, batch_size=100)
            for stream in streams_to_create:
                self.stdout.write(self.style.SUCCESS(f"Created {stream}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully imported {len(streams_to_create)} streaming sessions "
                f"(skipped: {skipped_count}).",
            ),
        )

    def _report_dry_run(self, s_df: pd.DataFrame, event: Event | None = None) -> None:
        """Print what would be imported without touching the database."""
        valid_count = 0
        invalid_count = 0

        for _, row in s_df.iterrows():
            room = self.get_room(row[COL_ROOM], event)
            if room:
                valid_count += 1
                self.stdout.write(
                    f"Would process: Room={room.name}, "
                    f"Start={row[COL_START_TIME]}, End={row[COL_END_TIME]}",
                )
            else:
                invalid_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Dry run completed. Would process {valid_count} streaming sessions "
                f"(would skip: {invalid_count}).",
            ),
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Execute the command to import streaming sessions from Google Sheets."""
        sheet_id = options["livestreams_sheet_id"]
        worksheet_name = options["livestreams_worksheet_name"]
        dry_run = options.get("dry_run", False)

        event_slug = (options.get("event_slug") or "").strip()
        event = Event.objects.filter(slug=event_slug).first() if event_slug else None
        if event_slug and event is None:
            self.stdout.write(
                self.style.WARNING(
                    f"Event '{event_slug}' not found; falling back to unscoped (global) import.",
                ),
            )

        try:
            if dry_run:
                self.stdout.write(self.style.NOTICE("DRY RUN: No database changes will be made"))

            s_df = self.fetch_spreadsheet_data(sheet_id, worksheet_name)
            self.stdout.write(f"Found {len(s_df)} potential streaming sessions")

            if dry_run:
                self._report_dry_run(s_df, event)
            else:
                self._import_streams(s_df, event)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Command failed: {exc!s}"))
            logger.exception("Error importing streaming sessions")
            raise
