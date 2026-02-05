"""Management command for filling the live streams from Google Sheets."""

import logging
from typing import Any

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from talks.models import Room, Streaming


logger = logging.getLogger(__name__)


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
            "--dry-run",
            action="store_true",
            help="Perform a dry run without making database changes",
        )

    def fetch_spreadsheet_data(self, sheet_id: str, worksheet_name: str) -> pd.DataFrame:
        """Fetch and process data from Google Sheets."""
        try:
            url = f"https://docs.google.com/spreadsheet/ccc?key={sheet_id}&output=xlsx"
            self.stdout.write("Fetching data from Google Sheets...")

            s_df = pd.read_excel(url, sheet_name=worksheet_name)
            s_df = s_df[(s_df["Vimeo / Restream"] == "Vimeo") & (s_df["Embed Link"].notna())]
            s_df = s_df[["Room", "Start Time", "End Time", "Embed Link"]]

            # Localize timestamps
            s_df["Start Time"] = pd.to_datetime(s_df["Start Time"]).dt.tz_localize("Europe/Berlin")
            s_df["End Time"] = pd.to_datetime(s_df["End Time"]).dt.tz_localize("Europe/Berlin")
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Error fetching spreadsheet data: {exc}"))
            raise
        else:
            return s_df

    def get_room(self, room_name: str) -> Room | None:
        """Get room by name or return None if not found."""
        room_name = room_name.strip()
        return Room.objects.filter(name=room_name).first()

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Execute the command to import streaming sessions from Google Sheets."""
        # Extract options
        sheet_id = options["livestreams_sheet_id"]
        worksheet_name = options["livestreams_worksheet_name"]
        dry_run = options.get("dry_run", False)

        try:
            if dry_run:
                self.stdout.write(self.style.NOTICE("DRY RUN: No database changes will be made"))

            # Fetch data
            s_df = self.fetch_spreadsheet_data(sheet_id, worksheet_name)
            self.stdout.write(f"Found {len(s_df)} potential streaming sessions")

            # Handle database operations
            if not dry_run:
                # Clear existing records
                deleted_count = Streaming.objects.all().delete()[0]
                self.stdout.write(
                    self.style.WARNING(f"Deleted {deleted_count} existing streaming sessions"),
                )

                # Prepare for bulk operations
                streams_to_create = []
                skipped_count = 0

                for _, row in s_df.iterrows():
                    room = self.get_room(row["Room"])

                    if not room:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Room '{row['Room'].strip()}' not found. Skipping this row.",
                            ),
                        )
                        skipped_count += 1
                        continue

                    streams_to_create.append(
                        Streaming(
                            room=room,
                            start_time=row["Start Time"],
                            end_time=row["End Time"],
                            video_link=row["Embed Link"],
                        ),
                    )

                # Perform bulk creation
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
            else:
                # Dry run reporting
                valid_count = 0
                invalid_count = 0

                for _, row in s_df.iterrows():
                    room = self.get_room(row["Room"])
                    if room:
                        valid_count += 1
                        self.stdout.write(
                            f"Would process: Room={room.name}, "
                            f"Start={row['Start Time']}, End={row['End Time']}",
                        )
                    else:
                        invalid_count += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Dry run completed. Would process {valid_count} streaming sessions "
                        f"(would skip: {invalid_count}).",
                    ),
                )

        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Command failed: {exc!s}"))
            logger.exception("Error importing streaming sessions")
            raise
