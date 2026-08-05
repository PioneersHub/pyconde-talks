"""
Saved-talk views: the toggle, and the merge of bookmarks made while logged out.

Split out from ``talks.views`` so the bookmark endpoints are isolated from the browsing and rating
views.
"""

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .models import SavedTalk, Talk
from .utils import is_htmx_request


if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

    from users.models import CustomUser


# Upper bound on a merge payload, matching the cap the browser applies when storing. Bounded so
# a hand-crafted request cannot make the server look up an unlimited number of ids.
MAX_MERGE_IDS = 500


@require_POST
def toggle_save_talk(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Toggle a talk's saved/bookmarked status for the current user.

    If the talk is already saved, it removes the saved status. Otherwise, it saves the talk. Returns
    an HTMX partial with the updated bookmark button.
    """
    user = cast("CustomUser", request.user)
    talk = get_object_or_404(Talk.objects.accessible_to(user), pk=talk_id)
    saved_talk, created = SavedTalk.objects.get_or_create(
        user=request.user,
        talk=talk,
    )

    if not created:
        saved_talk.delete()

    is_saved = created
    is_htmx = is_htmx_request(request)

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


def _parse_merge_ids(body: bytes) -> list[int] | None:
    """Return the talk ids from a merge payload, or None when it is not usable."""
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    raw_ids = payload.get("ids", [])
    if not isinstance(raw_ids, list) or len(raw_ids) > MAX_MERGE_IDS:
        return None
    # ``True`` is an int in Python, so exclude bools explicitly rather than by type alone.
    return [i for i in raw_ids if isinstance(i, int) and not isinstance(i, bool) and i > 0]


@login_required
@require_POST
def merge_saved_talks(request: HttpRequest) -> JsonResponse:
    """
    Fold bookmarks made while logged out into the account.

    Called once by ``saved-talks.js`` on the first authenticated page view after a login that
    followed anonymous browsing. It has to be client-initiated: the bookmarks live in localStorage,
    which a login signal on the server cannot see.

    Ids are scoped through ``accessible_to``, so a hand-crafted payload cannot be used to probe
    which talk ids exist on an event the user cannot see. The merge is a union and never deletes, so
    an account's own bookmarks always win, and it is idempotent: the client clears its copy only on
    a 200, and retries on the next page view otherwise.
    """
    talk_ids = _parse_merge_ids(request.body)
    if talk_ids is None:
        return JsonResponse({"error": "invalid payload"}, status=HTTPStatus.BAD_REQUEST)

    user = cast("CustomUser", request.user)
    accessible = set(
        Talk.objects.accessible_to(user).filter(pk__in=talk_ids).values_list("pk", flat=True),
    )
    existing = SavedTalk.talk_ids_for(user)
    to_add = accessible - existing

    SavedTalk.objects.bulk_create(
        [SavedTalk(user=user, talk_id=pk) for pk in to_add],
        # Belt and braces against a double submit racing itself: the unique constraint on
        # (user, talk) already prevents duplicates, and this keeps the race from being a 500.
        ignore_conflicts=True,
    )

    return JsonResponse({"merged": len(to_add), "saved": sorted(accessible | existing)})
