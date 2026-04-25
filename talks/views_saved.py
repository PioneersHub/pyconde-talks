"""
Saved-talk toggle view.

Split out from ``talks.views`` so the bookmark toggle endpoint is isolated from the browsing and
rating views.
"""

from typing import TYPE_CHECKING

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .models import SavedTalk, Talk


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse


@require_POST
def toggle_save_talk(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Toggle a talk's saved/bookmarked status for the current user.

    If the talk is already saved, it removes the saved status. Otherwise, it saves the talk.
    Returns an HTMX partial with the updated bookmark button.
    """
    talk = get_object_or_404(Talk, pk=talk_id)
    saved_talk, created = SavedTalk.objects.get_or_create(
        user=request.user,
        talk=talk,
    )

    if not created:
        saved_talk.delete()

    is_saved = created
    is_htmx = request.headers.get("HX-Request") == "true"

    if is_htmx:
        # Schedule cards use a compact icon-only partial (no text label).
        hx_target = request.headers.get("HX-Target", "")
        template = (
            "talks/partials/schedule_save_button.html"
            if hx_target.startswith("sched-save-")
            else "talks/partials/save_button.html"
        )
        return render(
            request,
            template,
            {"talk": talk, "is_saved": is_saved},
        )

    if is_saved:
        messages.success(request, _("Talk saved!"))
    else:
        messages.info(request, _("Talk removed from saved."))
    return redirect("talk_detail", pk=talk_id)
