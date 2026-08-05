"""
Management command for setting talk recordings from a Pretalx code to YouTube ID map.

The input is a JSON object mapping each talk's Pretalx submission code to the ID of the YouTube
video holding its recording:

    {"KFPNUA": "Z7Xlj2eG8sc", ...}

Talks are matched on ``Talk.pretalx_code``, the last path segment of ``Talk.pretalx_link``, so the
map needs no database IDs and can be produced straight from the YouTube upload spreadsheet.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from events.models import Event
from talks.models import Talk
from utils.url import YOUTUBE_ID_PATTERN, youtube_video_id


# cspell:ignore KFPNUA

# Talks store the short form the map is written for. Nothing here has to care whether the player
# can frame it: ``Talk.get_video_link`` converts to the embeddable URL on the way to the template.
YOUTUBE_URL_TEMPLATE = "https://youtu.be/{video_id}"

# How many still-missing codes to spell out in the summary before switching to a count.
MAX_LISTED_CODES = 20


@dataclass(frozen=True)
class UpdatePlan:
    """What to do with links that are already on the talks being updated."""

    dry_run: bool = False
    skip_existing: bool = False


@dataclass
class UpdateStats:
    """Per-code outcome counts, reported as a summary at the end of the run."""

    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    not_found: int = 0
    ambiguous: int = 0
    invalid: int = 0
    missing_codes: list[str] = field(default_factory=list)


class Command(BaseCommand):
    """Update talk video links from a JSON map of Pretalx codes to YouTube IDs."""

    help = "Update talk video links from a JSON map of Pretalx codes to YouTube video IDs."

    def add_arguments(self, parser: CommandParser) -> None:
        """Add command line arguments."""
        parser.add_argument(
            "json_file",
            type=str,
            help="Path to a JSON file mapping Pretalx codes to YouTube video IDs",
        )
        parser.add_argument(
            "--event-slug",
            type=str,
            default=getattr(settings, "DEFAULT_EVENT", ""),
            help="Only update talks of this event (default: DEFAULT_EVENT). Pass an empty "
            "string to match talks of every event.",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Leave talks that already have a video link untouched",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without making database changes",
        )

    def load_map(self, path: Path) -> dict[str, str]:
        """Read the Pretalx code to YouTube ID map from *path*."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            msg = f"Could not read {path}: {exc}"
            raise CommandError(msg) from exc
        except json.JSONDecodeError as exc:
            msg = f"{path} is not valid JSON: {exc}"
            raise CommandError(msg) from exc

        if not isinstance(raw, dict):
            msg = f"{path} must contain a JSON object mapping Pretalx codes to YouTube IDs."
            raise CommandError(msg)

        return {str(code).strip(): str(video_id).strip() for code, video_id in raw.items()}

    def resolve_event(self, event_slug: str) -> Event | None:
        """
        Return the event to scope the update to, or None for an unscoped update.

        A slug that does not resolve is an operator error (a typo, or an event that was never
        imported): abort rather than silently widening the update to every event's talks.
        """
        event_slug = (event_slug or "").strip()
        if not event_slug:
            return None

        event = Event.objects.filter(slug=event_slug).first()
        if event is None:
            msg = (
                f"Event '{event_slug}' not found. Pass an existing --event-slug, or an empty "
                "one (--event-slug '') to update talks of every event."
            )
            raise CommandError(msg)
        return event

    def talks_by_code(self, event: Event | None) -> dict[str, list[Talk]]:
        """
        Group the talks that have a Pretalx link by their Pretalx code.

        One query for the whole event, instead of one per entry in the map. Codes are kept in a list
        rather than collapsed to a single talk so an ambiguous code can be reported and skipped
        instead of overwriting an arbitrary one of its matches.
        """
        talks = Talk.objects.exclude(pretalx_link="")
        if event is not None:
            talks = talks.filter(event=event)

        by_code: dict[str, list[Talk]] = defaultdict(list)
        for talk in talks:
            if code := talk.pretalx_code:
                by_code[code].append(talk)
        return by_code

    def update_video_links(
        self,
        video_ids: dict[str, str],
        by_code: dict[str, list[Talk]],
        plan: UpdatePlan | None = None,
    ) -> UpdateStats:
        """Point every talk named in *video_ids* at its YouTube recording."""
        plan = plan or UpdatePlan()
        stats = UpdateStats()

        for code, video_id in video_ids.items():
            if not YOUTUBE_ID_PATTERN.match(video_id):
                stats.invalid += 1
                self.stdout.write(
                    self.style.WARNING(f"{code}: '{video_id}' is not a YouTube video ID"),
                )
                continue
            talk = self._resolve_talk(code, by_code, stats)
            if talk is not None:
                self._update_talk(talk, video_id, plan, stats)

        stats.missing_codes = sorted(
            code
            for code, talks in by_code.items()
            if code not in video_ids and any(not talk.video_link for talk in talks)
        )
        return stats

    def _resolve_talk(
        self,
        code: str,
        by_code: dict[str, list[Talk]],
        stats: UpdateStats,
    ) -> Talk | None:
        """Return the one talk with this Pretalx code, or None when there is no single match."""
        matches = by_code.get(code, [])
        if not matches:
            stats.not_found += 1
            self.stdout.write(self.style.WARNING(f"{code}: no talk with this Pretalx code"))
            return None
        if len(matches) > 1:
            stats.ambiguous += 1
            self.stdout.write(
                self.style.WARNING(
                    f"{code}: {len(matches)} talks share this Pretalx code; skipping to avoid "
                    "clobbering the wrong one. Narrow the run with --event-slug.",
                ),
            )
            return None
        return matches[0]

    def _update_talk(
        self,
        talk: Talk,
        video_id: str,
        plan: UpdatePlan,
        stats: UpdateStats,
    ) -> None:
        """Store the recording link on *talk*, unless it is already there or must be kept."""
        if youtube_video_id(talk.video_link) == video_id:
            stats.unchanged += 1
            return

        if plan.skip_existing and talk.video_link:
            stats.skipped += 1
            self.stdout.write(
                self.style.NOTICE(f"{talk.pretalx_code}: keeping existing link on '{talk.title}'"),
            )
            return

        video_link = YOUTUBE_URL_TEMPLATE.format(video_id=video_id)
        stats.updated += 1
        self.stdout.write(f"{talk.pretalx_code}: {video_link} -> '{talk.title}'")
        if plan.dry_run:
            return

        talk.video_link = video_link
        # These are per-talk uploads, so the recording starts at the talk. Any offset stored for
        # the livestream cut it replaces no longer applies.
        talk.video_start_time = 0
        talk.save(update_fields=["video_link", "video_start_time", "updated_at"])

    def report(self, stats: UpdateStats, *, dry_run: bool) -> None:
        """Print the run summary."""
        verb = "would be updated" if dry_run else "updated"
        self.stdout.write(f"{stats.updated} talk(s) {verb}, {stats.unchanged} already current")

        for count, label in (
            (stats.skipped, "kept their existing link (--skip-existing)"),
            (stats.not_found, "code(s) matched no talk"),
            (stats.ambiguous, "code(s) matched more than one talk"),
            (stats.invalid, "invalid YouTube ID(s) in the map"),
        ):
            if count:
                self.stdout.write(self.style.WARNING(f"{count} {label}"))

        if stats.missing_codes:
            listed = ", ".join(stats.missing_codes[:MAX_LISTED_CODES])
            rest = len(stats.missing_codes) - MAX_LISTED_CODES
            suffix = f", and {rest} more" if rest > 0 else ""
            self.stdout.write(
                self.style.NOTICE(
                    f"{len(stats.missing_codes)} talk(s) still have no recording and are absent "
                    f"from the map: {listed}{suffix}",
                ),
            )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ARG002
        """Execute the command to update video links from the JSON map."""
        dry_run = options.get("dry_run", False)
        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN: No database changes will be made"))

        event = self.resolve_event(options.get("event_slug", ""))
        path = Path(options["json_file"])
        video_ids = self.load_map(path)
        scope = f"event '{event.slug}'" if event else "all events"
        self.stdout.write(f"Loaded {len(video_ids)} Pretalx code(s) from {path}, matching {scope}")

        plan = UpdatePlan(
            dry_run=dry_run,
            skip_existing=options.get("skip_existing", False),
        )
        stats = self.update_video_links(video_ids, self.talks_by_code(event), plan)
        self.report(stats, dry_run=dry_run)
        self.stdout.write(self.style.SUCCESS("Dry run completed" if dry_run else "Done"))
