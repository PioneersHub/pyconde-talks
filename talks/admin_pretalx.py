"""
Admin for ``PendingPretalxChange`` rows recorded by the detect-only importer.

Provides a triage list with status / kind / event filters, bulk *apply* and
*dismiss* actions, and a "Check Pretalx now" button that re-runs the
detect-only importer for the selected event.
"""

from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.contrib import admin, messages
from django.core.management import CommandError, call_command
from django.http import HttpResponseRedirect
from django.urls import URLPattern, path, reverse
from django.utils.translation import gettext_lazy as _

from talks.management.commands._pretalx.apply import apply_change
from talks.models import PendingPretalxChange


if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest, HttpResponse
    from django_stubs_ext import StrOrPromise

    from users.models import CustomUser


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
    """Triage view for detected Pretalx diffs with bulk apply/dismiss and a re-check button."""

    change_list_template = "admin/talks/pendingpretalxchange/change_list.html"
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
    actions = ("apply_changes", "dismiss_changes")

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

    # ------------------------------------------------------------------
    # Bulk actions
    # ------------------------------------------------------------------

    @admin.action(description=_("Apply selected pending Pretalx changes"))
    def apply_changes(
        self,
        request: HttpRequest,
        queryset: QuerySet[PendingPretalxChange],
    ) -> None:
        """Apply each *pending* row in *queryset*; skip rows that are already closed."""
        applied = 0
        skipped = 0
        failed = 0
        for change in queryset:
            if not change.is_pending:
                skipped += 1
                continue
            try:
                apply_change(change, user=cast("CustomUser", request.user))
                applied += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.message_user(
                    request,
                    _("Failed to apply %(code)s: %(error)s")
                    % {"code": change.pretalx_code, "error": exc},
                    level=messages.ERROR,
                )
        if applied:
            self.message_user(
                request,
                _("Applied %(n)d pending change(s).") % {"n": applied},
                level=messages.SUCCESS,
            )
        if skipped:
            self.message_user(
                request,
                _("%(n)d already-closed row(s) were left untouched.") % {"n": skipped},
                level=messages.INFO,
            )
        if failed and not applied:
            # Always surface at least one message so the admin sees feedback.
            self.message_user(request, _("No changes were applied."), level=messages.WARNING)

    @admin.action(description=_("Dismiss selected pending Pretalx changes"))
    def dismiss_changes(
        self,
        request: HttpRequest,
        queryset: QuerySet[PendingPretalxChange],
    ) -> None:
        """Mark each *pending* row in *queryset* as dismissed; skip closed rows."""
        dismissed = 0
        for change in queryset:
            if not change.is_pending:
                continue
            change.mark_dismissed(user=cast("CustomUser", request.user))
            dismissed += 1
        self.message_user(
            request,
            _("Dismissed %(n)d pending change(s).") % {"n": dismissed},
            level=messages.SUCCESS,
        )

    # ------------------------------------------------------------------
    # "Check Pretalx now" button
    # ------------------------------------------------------------------

    def get_urls(self) -> list[URLPattern]:
        """Extend the default admin URLs with the re-detect endpoint."""
        urls = super().get_urls()
        return [
            path(
                "check-pretalx-now/",
                self.admin_site.admin_view(self.check_pretalx_now),
                name="talks_pendingpretalxchange_check_now",
            ),
            *urls,
        ]

    def check_pretalx_now(self, request: HttpRequest) -> HttpResponse:
        """
        Run the detect-only importer for the default event and return to the list.

        Synchronous on purpose: the importer is API-bound (tens of seconds for a
        500-talk event) but small enough to block one admin request. Surfacing
        progress would need a task queue; that's deferred to keep the deploy
        footprint flat.
        """
        event_slug = getattr(settings, "DEFAULT_EVENT", "")
        redirect_to = reverse("admin:talks_pendingpretalxchange_changelist")
        if not event_slug:
            self.message_user(
                request,
                _("DEFAULT_EVENT is not configured; cannot run the Pretalx detect."),
                level=messages.ERROR,
            )
            return HttpResponseRedirect(redirect_to)

        try:
            call_command(
                "import_pretalx_talks",
                "--detect-only",
                f"--event-slug={event_slug}",
                verbosity=1,
            )
        except CommandError as exc:
            self.message_user(
                request,
                _("Pretalx detect failed: %(error)s") % {"error": exc},
                level=messages.ERROR,
            )
        else:
            self.message_user(
                request,
                _("Pretalx detect complete. New or updated rows (if any) are listed below."),
                level=messages.SUCCESS,
            )
        return HttpResponseRedirect(redirect_to)
