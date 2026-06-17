"""Middleware that applies an authenticated user's saved language preference."""

from typing import TYPE_CHECKING

from django.utils import translation


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest, HttpResponse


class UserLanguageMiddleware:
    """
    Activate the logged-in user's ``preferred_language`` for the request.

    Django 6 no longer stores the active language in the session, so a profile preference cannot be
    pushed there at login time. Instead we re-resolve it on every request: ``LocaleMiddleware`` has
    already picked a language from the cookie/Accept-Language, and this middleware overrides it for
    authenticated users who set an explicit preference, so their choice follows the account across
    browsers and devices.

    Must run after ``AuthenticationMiddleware`` (it needs ``request.user``). Users with no stored
    preference are left on whatever ``LocaleMiddleware`` resolved.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next callable in the middleware chain."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Override the active language from the user's profile, then continue the chain."""
        user = getattr(request, "user", None)
        language = getattr(user, "preferred_language", "") if user is not None else ""
        if language:
            translation.activate(language)
            request.LANGUAGE_CODE = translation.get_language()
        return self.get_response(request)
