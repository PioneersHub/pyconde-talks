"""
Management command for updating the video links with rough cuts from Vimeo.

Assumes the video name is in the format {pretalx_id}_{title}.
"""

from itertools import chain
from typing import Any

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from talks.models import Talk


class Command(BaseCommand):
    """Update video links with rough cuts from Vimeo."""

    help = "Update video links with rough cuts from Vimeo."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command line arguments."""
        parser.add_argument(
            "--vimeo-access-token",
            type=str,
            default=settings.VIMEO_ACCESS_TOKEN,
            help="Vimeo access token",
        )
        parser.add_argument(
            "--vimeo-project-ids",
            type=str,
            default=settings.VIMEO_PROJECT_IDS,
            help="Vimeo project IDS (comma-separated)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Perform a dry run without making database changes",
        )

    def fetch_single_folder(self, access_token: str, project_id: str) -> dict[str, str]:
        """
        Fetch information about all videos inside a single Vimeo folder.

        Returns a map between the video names and their embed URL.
        """
        url = f"https://api.vimeo.com/me/projects/{project_id}/videos"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        params = {
            "fields": "name,player_embed_url",
        }
        response = httpx.get(url, headers=headers, params=params)
        response.raise_for_status()
        videos = response.json().get("data", [])
        self.stdout.write(f"Fetched {len(videos)} videos from folder {project_id}")
        return {video["name"]: video["player_embed_url"] for video in videos}

    def fetch_vimeo_data(self, access_token: str, project_ids: list[str]) -> dict[str, str]:
        """Fetch video data from Vimeo."""
        return dict(
            chain.from_iterable(
                self.fetch_single_folder(access_token, project_id.strip()).items()
                for project_id in project_ids
            ),
        )

    def update_video_links(self, vimeo_data: dict[str, str]) -> None:
        """Update video links in the database."""
        for name, video_link in vimeo_data.items():
            pretalx_id = name.split("_")[0]
            talk = Talk.objects.filter(pretalx_link__contains=pretalx_id).first()
            if talk:
                talk.video_link = video_link
                talk.video_start_time = 0
                self.stdout.write(self.style.NOTICE(f"Updating talk: {talk.title}"))
                talk.save()
            else:
                self.stdout.write(
                    self.style.WARNING(f"Talk not found for pretalx ID: {pretalx_id}"),
                )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Execute the command to import streaming sessions from Google Sheets."""
        # Extract options
        vimeo_access_token = options["vimeo_access_token"]
        vimeo_project_ids = options["vimeo_project_ids"]
        dry_run = options.get("dry_run", False)

        try:
            if dry_run:
                self.stdout.write(self.style.NOTICE("DRY RUN: No database changes will be made"))

            # Fetch data
            vimeo_data = self.fetch_vimeo_data(vimeo_access_token, vimeo_project_ids.split(","))
            self.stdout.write(f"Found {len(vimeo_data)} videos")

            # Update video links
            if not dry_run:
                self.update_video_links(vimeo_data)
                self.stdout.write(
                    self.style.SUCCESS("Successfully updated video links"),
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS("Dry run completed"),
                )

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Command failed: {e!s}"))
            raise
