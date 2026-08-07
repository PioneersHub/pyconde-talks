"""
Template tags for the mobile tab bar.

Conference attendees are mostly on phones, so below the ``md`` breakpoint the primary navigation is
a fixed bottom tab bar instead of the header links. Which tabs exist depends on the visitor, and
which one is marked current depends on the view being rendered, so both live here rather than in a
pile of ``{% if %}`` branches in the template.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django import template
from django.urls import reverse
from django.utils.translation import gettext as _


if TYPE_CHECKING:
    from django.http import HttpRequest


register = template.Library()


@dataclass(frozen=True)
class NavItem:
    """One tab: where it goes, which icon it draws, and whether it is the current one."""

    label: str
    url: str
    icon: str
    is_active: bool


# Views that belong to a tab. Drilling into a talk or its Q&A keeps the Talks tab marked, the way a
# native tab bar keeps highlighting the tab you came from.
_TALK_VIEWS = frozenset(
    {
        "talk_list",
        "talk_detail",
        "talk_redirect",
        "talk_questions",
        "question_redirect",
        "question_create",
        "question_edit",
    },
)
_ACCOUNT_VIEWS = frozenset(
    {
        "account_confirm_login_code",
        "account_login",
        "account_logout",
        "account_request_login_code",
        "add_email",
        "confirm_add_email",
        "delete_account",
        "socialaccount_connections",
        "user_profile",
    },
)


def _current_view_name(request: HttpRequest) -> str:
    """Return the url_name being rendered, or "" when there is no match (error pages)."""
    match = request.resolver_match
    return match.url_name or "" if match else ""


def build_nav_items(request: HttpRequest, *, has_public_event: bool) -> list[NavItem]:
    """
    Build the tab list for this visitor.

    Talks and Schedule are hidden from a logged-out visitor when no event is public, for the same
    reason the header hides them: both links would lead to an empty page. Saved needs an account,
    because an anonymous visitor's bookmarks live in localStorage and the server cannot filter on
    what it cannot see.
    """
    view_name = _current_view_name(request)
    saved_only = request.GET.get("saved") == "1"
    authenticated = request.user.is_authenticated

    items = [NavItem(_("Home"), reverse("home"), "home", view_name == "home")]

    if authenticated or has_public_event:
        items += [
            NavItem(
                _("Talks"),
                reverse("talk_list"),
                "rectangle-stack",
                view_name in _TALK_VIEWS and not saved_only,
            ),
            NavItem(_("Schedule"), reverse("schedule"), "calendar", view_name == "schedule"),
        ]

    if authenticated:
        items += [
            NavItem(
                _("Saved"),
                f"{reverse('talk_list')}?saved=1",
                "bookmark",
                view_name == "talk_list" and saved_only,
            ),
            NavItem(
                _("Profile"),
                reverse("user_profile"),
                "user-circle",
                view_name in _ACCOUNT_VIEWS,
            ),
        ]
    else:
        items.append(
            NavItem(
                _("Sign in"),
                reverse("account_login"),
                "user-circle",
                view_name in _ACCOUNT_VIEWS,
            ),
        )

    return items


@register.inclusion_tag("partials/bottom_nav.html", takes_context=True)
def bottom_nav(context: template.Context) -> dict[str, list[NavItem]]:
    """Render the mobile tab bar."""
    has_public_event = bool(context.get("has_public_event"))
    return {"nav_items": build_nav_items(context["request"], has_public_event=has_public_event)}
