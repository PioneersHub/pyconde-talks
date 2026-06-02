"""
Pending-change tracking for the Pretalx importer's detect-only mode.

When the importer runs with ``--detect-only`` it records every diff it would have
applied as a :class:`PendingPretalxChange` row instead of touching the live
``Talk`` rows. Admins review those rows and apply or dismiss them on their own
schedule. Split out from ``talks.models`` to keep the rating/talk file focused;
imported back from ``talks/models.py`` so Django picks it up during migration
autodiscovery.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


if TYPE_CHECKING:
    from users.models import CustomUser


#: Upper bound for the Pretalx code stored on the pending row. Submissions use 6-char
#: codes but Pretalx has historically widened them, so leave headroom.
MAX_PRETALX_CODE_LENGTH = 32


class PendingPretalxChange(models.Model):
    """
    A diff detected by the Pretalx importer that has not yet been applied locally.

    One row per ``(event, pretalx_code)`` while still pending. Re-detecting the same
    submission updates the existing row's diff/payload and bumps
    :attr:`last_detected_at`. Applying or dismissing the row sets the matching
    timestamp; a fresh detection after that creates a new pending row.
    """

    class Kind(models.TextChoices):
        """High-level shape of the change."""

        CREATE = "create", _("Create")
        UPDATE = "update", _("Update")
        DELETE = "delete", _("Delete")

    event = models.ForeignKey(
        "events.Event",
        on_delete=models.CASCADE,
        related_name="pending_pretalx_changes",
        help_text=_("Event this submission belongs to"),
    )
    pretalx_code = models.CharField(
        max_length=MAX_PRETALX_CODE_LENGTH,
        help_text=_("Pretalx submission code (e.g. 'ABC123')"),
    )
    talk = models.ForeignKey(
        "talks.Talk",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_pretalx_changes",
        help_text=_("Local talk this change targets (null for CREATE)"),
    )
    kind = models.CharField(
        max_length=10,
        choices=Kind.choices,
        help_text=_("Whether this change would create, update, or delete the talk"),
    )
    field_diffs = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Per-field diff: {field_name: {old: <value>, new: <value>}}. Empty for CREATE/DELETE.",
        ),
    )
    speaker_diffs = models.JSONField(
        default=dict,
        blank=True,
        help_text=_("Speaker M2M diff: {added: [{code, name}], removed: [...]}."),
    )
    pretalx_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text=_(
            "Full snapshot of the new values to apply. Self-contained so applying does"
            " not require re-fetching Pretalx.",
        ),
    )
    first_detected_at = models.DateTimeField(
        default=timezone.now,
        help_text=_("When this diff was first observed"),
    )
    last_detected_at = models.DateTimeField(
        auto_now=True,
        help_text=_("Most recent detect run that confirmed this diff"),
    )
    applied_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the change was applied to the local Talk (null = still pending)"),
    )
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="applied_pretalx_changes",
        help_text=_("Admin who applied this change"),
    )
    dismissed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the change was dismissed (null = still pending)"),
    )
    dismissed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dismissed_pretalx_changes",
        help_text=_("Admin who dismissed this change"),
    )

    class Meta:
        """Metadata options for PendingPretalxChange."""

        verbose_name = _("Pending Pretalx change")
        verbose_name_plural = _("Pending Pretalx changes")
        ordering: ClassVar[list[str]] = ["-last_detected_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            # At most one open (neither applied nor dismissed) row per submission per
            # event. Re-detection of the same diff updates the existing row instead of
            # creating duplicates.
            models.UniqueConstraint(
                fields=["event", "pretalx_code"],
                condition=Q(applied_at__isnull=True, dismissed_at__isnull=True),
                name="unique_open_pending_change_per_submission",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["event", "applied_at", "dismissed_at"]),
            models.Index(fields=["pretalx_code"]),
            models.Index(fields=["-last_detected_at"]),
        ]

    def __str__(self) -> str:
        """Return a short human-readable identifier for admin lists."""
        # ``get_kind_display`` is auto-generated by Django but invisible to mypy/zuban;
        # render the value directly to keep the type checker happy.
        return f"{self.kind.upper()} {self.pretalx_code} ({self.event.slug})"

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @property
    def is_pending(self) -> bool:
        """Return ``True`` when neither apply nor dismiss has happened yet."""
        return self.applied_at is None and self.dismissed_at is None

    @property
    def is_applied(self) -> bool:
        """Return ``True`` when an admin has applied this change."""
        return self.applied_at is not None

    @property
    def is_dismissed(self) -> bool:
        """Return ``True`` when an admin has dismissed this change."""
        return self.dismissed_at is not None

    def mark_applied(self, user: CustomUser | None = None) -> None:
        """Record that *user* applied this change just now and save the row."""
        self.applied_at = timezone.now()
        self.applied_by = user
        self.save(update_fields=["applied_at", "applied_by", "last_detected_at"])

    def mark_dismissed(self, user: CustomUser | None = None) -> None:
        """Record that *user* dismissed this change just now and save the row."""
        self.dismissed_at = timezone.now()
        self.dismissed_by = user
        self.save(update_fields=["dismissed_at", "dismissed_by", "last_detected_at"])

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def summarize(self) -> str:
        """
        Return a short, human-readable description of what would change.

        Used in admin lists and the email digest. Keep it boring and grep-able.
        """
        if self.kind == self.Kind.CREATE:
            title = (self.pretalx_payload or {}).get("title", "")
            return f"NEW talk '{title}' ({self.pretalx_code})"
        if self.kind == self.Kind.DELETE:
            talk_title = self.talk.title if self.talk else self.pretalx_code
            return f"DELETE talk '{talk_title}'"
        # UPDATE
        fields = sorted(self.field_diffs)
        speaker_bits: list[str] = []
        added: list[dict[str, Any]] = self.speaker_diffs.get("added", [])
        removed: list[dict[str, Any]] = self.speaker_diffs.get("removed", [])
        if added:
            speaker_bits.append(f"+{len(added)} speaker(s)")
        if removed:
            speaker_bits.append(f"-{len(removed)} speaker(s)")
        change_bits = [*fields, *speaker_bits]
        joined = ", ".join(change_bits) if change_bits else "no field changes"
        talk_title = self.talk.title if self.talk else self.pretalx_code
        return f"UPDATE '{talk_title}': {joined}"
