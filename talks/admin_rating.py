"""
Admin configuration for Rating and SavedTalk models.

Split out from ``talks.admin`` to parallel the ``views_rating`` / ``models`` separation.
"""

from typing import TYPE_CHECKING, Any, ClassVar

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Rating, SavedTalk


if TYPE_CHECKING:
    from django.db.models import QuerySet
    from django.http import HttpRequest
    from django_stubs_ext import StrOrPromise


class HasCommentFilter(admin.SimpleListFilter):
    """Filter ratings by whether the ``comment`` text field is populated."""

    title = _("Has comment")
    parameter_name = "has_comment"

    def lookups(
        self,
        request: HttpRequest,  # noqa: ARG002
        model_admin: Any,  # noqa: ARG002
    ) -> list[tuple[str, StrOrPromise]]:
        """Return the two-choice filter options."""
        return [
            ("yes", _("With comment")),
            ("no", _("Without comment")),
        ]

    def queryset(
        self,
        request: HttpRequest,  # noqa: ARG002
        queryset: QuerySet[Rating],
    ) -> QuerySet[Rating]:
        """Keep only ratings with or without a non-empty ``comment``."""
        if self.value() == "yes":
            return queryset.exclude(comment="")
        if self.value() == "no":
            return queryset.filter(comment="")
        return queryset


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin[Rating]):
    """
    Admin configuration for the Rating model.

    Displays ratings with their comments (visible only to admins).

    Attributes:
        list_display: Fields to display in the admin list view.
        list_filter: Fields to filter by in the admin list view.
        search_fields: Fields to search by in the admin list view.
        readonly_fields: Fields that cannot be edited in the admin.
        list_select_related: Related models to prefetch for list view optimization.

    """

    list_display = ("talk", "user", "score", "has_comment", "created_at")
    list_filter = (HasCommentFilter, "score", "created_at")
    search_fields = ("talk__title", "user__email", "comment")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("talk", "user")

    fieldsets: ClassVar[list[Any]] = [
        (
            None,
            {
                "fields": ("talk", "user", "score", "comment"),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    ]

    @admin.display(boolean=True, description=_("Has Comment"))
    def has_comment(self, obj: Rating) -> bool:
        """Display whether the rating has a comment."""
        return bool(obj.comment)


@admin.register(SavedTalk)
class SavedTalkAdmin(admin.ModelAdmin[SavedTalk]):
    """Admin configuration for the SavedTalk model."""

    list_display = ("user", "talk", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__email", "talk__title")
    raw_id_fields = ("user", "talk")
    readonly_fields = ("created_at",)
