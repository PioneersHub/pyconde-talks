"""
Rating-related views for Talk objects.

Split out from ``talks.views`` so the rating submission / deletion / stats endpoints live next to
each other without pulling the core Talk list/detail views along with them.
"""

from http import HTTPStatus
from typing import Any

from django.contrib import messages
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from .models import COMMENT_MAX_LENGTH, MAX_RATING_SCORE, MIN_RATING_SCORE, Rating, Talk
from .views import _can_see_rating_summary


def _rating_error_response(
    request: HttpRequest,
    talk_id: int,
    message: str,
    *,
    is_htmx: bool,
    status: int = HTTPStatus.UNPROCESSABLE_ENTITY,
) -> HttpResponse:
    """Return an error response for rating operations (HTMX fragment or redirect)."""
    if is_htmx:
        return HttpResponse(message, status=status)
    messages.error(request, message)
    return redirect("talk_detail", pk=talk_id)


def _render_rating_htmx_response(
    request: HttpRequest,
    talk: Talk,
    *,
    is_comment_save: bool,
) -> HttpResponse:
    """Render the rating widget and OOB title stars for an HTMX response."""
    stats = talk.get_rating_stats()
    user_rating = Rating.objects.filter(talk=talk, user=request.user).first()
    show_summary = _can_see_rating_summary(request.user, talk.event)
    context = {
        "talk": talk,
        "average_rating": stats.average if show_summary else None,
        "rating_count": stats.total if show_summary else 0,
        "user_rating": user_rating,
        "show_comment_form": not is_comment_save,
        "show_rating_summary": show_summary,
        # Preserve in-progress comment text on star clicks (not saved to DB)
        "draft_comment": request.POST.get("comment") if not is_comment_save else None,
    }
    widget_html = render(request, "talks/partials/rating_widget.html", context).content.decode()
    oob_html = render(
        request,
        "talks/partials/title_star_rating_oob.html",
        context,
    ).content.decode()
    return HttpResponse(widget_html + oob_html)


@require_POST
def rate_talk(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Handle talk rating submission.

    Users can rate a talk from 1 to 5 stars with an optional comment.
    If a rating already exists, it is updated.
    Returns a partial HTML fragment for HTMX requests or redirects otherwise.
    """
    talk = get_object_or_404(Talk, pk=talk_id)
    is_htmx = request.headers.get("HX-Request") == "true"
    is_comment_save = request.POST.get("save_comment") == "1"

    # Validate score
    try:
        score = int(request.POST.get("score"))  # type: ignore[arg-type]
    except TypeError, ValueError:  # fmt: skip
        return _rating_error_response(request, talk_id, _("Invalid rating value."), is_htmx=is_htmx)

    if score < MIN_RATING_SCORE or score > MAX_RATING_SCORE:
        return _rating_error_response(
            request,
            talk_id,
            _("Rating must be between %(min)s and %(max)s stars.")
            % {"min": MIN_RATING_SCORE, "max": MAX_RATING_SCORE},
            is_htmx=is_htmx,
        )

    # Build defaults: only update comment when explicitly saving it (HTMX star clicks skip comment)
    defaults: dict[str, Any] = {"score": score}
    if is_comment_save or not is_htmx:
        defaults["comment"] = request.POST.get("comment", "").strip()[:COMMENT_MAX_LENGTH]

    # Create or update rating
    try:
        _rating, created = Rating.objects.update_or_create(
            talk=talk,
            user=request.user,
            defaults=defaults,
        )
        if not is_htmx:
            msg = (
                _("Your rating has been submitted!")
                if created
                else _("Your rating has been updated!")
            )
            messages.success(request, msg)
    except IntegrityError:
        return _rating_error_response(
            request,
            talk_id,
            _("Error submitting rating. Please try again."),
            is_htmx=is_htmx,
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    if is_htmx:
        return _render_rating_htmx_response(request, talk, is_comment_save=is_comment_save)

    return redirect("talk_detail", pk=talk_id)


@require_POST
def delete_rating(request: HttpRequest, talk_id: int) -> HttpResponse:
    """
    Delete the current user's rating for a talk.

    Returns a partial HTML fragment for HTMX requests or redirects otherwise.
    """
    talk = get_object_or_404(Talk, pk=talk_id)
    is_htmx = request.headers.get("HX-Request") == "true"
    deleted_count, _detail = Rating.objects.filter(talk=talk, user=request.user).delete()

    if not is_htmx:
        if deleted_count:
            messages.success(request, _("Your rating has been removed."))
        else:
            messages.info(request, _("No rating to remove."))
        return redirect("talk_detail", pk=talk_id)

    return _render_rating_htmx_response(request, talk, is_comment_save=False)


@require_safe
def get_talk_rating_stats(request: HttpRequest, talk_id: int) -> JsonResponse:
    """
    Return rating statistics for a talk as JSON.

    Returns average_rating, rating_count, and the current user's rating if it exists.
    """
    talk = get_object_or_404(Talk, pk=talk_id)

    stats = talk.get_rating_stats()

    user_rating = None
    if request.user.is_authenticated:
        rating = Rating.objects.filter(talk=talk, user=request.user).first()
        if rating:
            user_rating = {
                "score": rating.score,
                "comment": rating.comment,
            }

    show_summary = _can_see_rating_summary(request.user, talk.event)
    return JsonResponse(
        {
            "average_rating": (round(stats.average, 1) if show_summary and stats.average else None),
            "rating_count": stats.total if show_summary else 0,
            "user_rating": user_rating,
        },
    )
