"""
Admin for ``PendingPretalxChange`` rows recorded by the detect-only importer.

Read-only list + filters for now. Apply/dismiss actions and the "Check Pretalx
now" button are wired in later commits to keep each change reviewable on its
own.
"""

from typing import TYPE_CHECKING

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from talks.models import PendingPretalxChange


if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django_stubs_ext import StrOrPromise


class PendingStatusFilter(admin.SimpleListFilter):
    """Pending / applied / dismissed - the natural way to triage the queue."""

    title = _("status")
    parameter_name = "status"

    def lookups(
        self,
        request: HttpRequest,  # noqa: ARG002
        model_admin: admin.ModelAdmin[PendingPretalxChange],  # noqa: ARG002
    ) -> list[tuple[str, StrOrPromise]]:
        """Return the (value, label) pairs shown in the filter sidebar."""
        return [
            ("pending", _("Pending")),
            ("applied", _("Applied")),
            ("dismissed", _("Dismissed")),
        ]

    def queryset(
        self,
        request: HttpRequest,  # noqa: ARG002
        queryset: QuerySet[PendingPretalxChange],
    ) -> QuerySet[PendingPretalxChange]:
        """Restrict to the picked status, or pass through when no filter is set."""
        match self.value():
            case "pending":
                return queryset.filter(applied_at__isnull=True, dismissed_at__isnull=True)
            case "applied":
                return queryset.filter(applied_at__isnull=False)
            case "dismissed":
                return queryset.filter(dismissed_at__isnull=False)
            case _:
                return queryset


@admin.register(PendingPretalxChange)
class PendingPretalxChangeAdmin(admin.ModelAdmin[PendingPretalxChange]):
    """Read-only triage view for detected-but-not-yet-applied Pretalx diffs."""

    list_display = (
        "pretalx_code",
        "event",
        "kind",
        "status_label",
        "summary",
        "first_detected_at",
        "last_detected_at",
    )
    list_filter = (PendingStatusFilter, "kind", "event")
    search_fields = ("pretalx_code", "talk__title")
    readonly_fields = (
        "event",
        "pretalx_code",
        "talk",
        "kind",
        "field_diffs",
        "speaker_diffs",
        "pretalx_payload",
        "first_detected_at",
        "last_detected_at",
        "applied_at",
        "applied_by",
        "dismissed_at",
        "dismissed_by",
    )
    autocomplete_fields = ("talk",)
    date_hierarchy = "last_detected_at"

    @admin.display(description=_("summary"))
    def summary(self, obj: PendingPretalxChange) -> str:
        """One-line description of the diff (kind + affected fields/speakers)."""
        return obj.summarize()

    @admin.display(description=_("status"), ordering="applied_at")
    def status_label(self, obj: PendingPretalxChange) -> StrOrPromise:
        """Pending / applied / dismissed for the list view."""
        if obj.is_applied:
            return _("Applied")
        if obj.is_dismissed:
            return _("Dismissed")
        return _("Pending")

    def has_add_permission(self, request: HttpRequest) -> bool:  # noqa: ARG002
        """Pending rows are only created by the importer, never by hand."""
        return False
