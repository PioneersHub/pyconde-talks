"""Admin interface for the talks app."""

from django.contrib import admin

from .models import Talk


@admin.register(Talk)
class TalkAdmin(admin.ModelAdmin):
    """Admin interface for the Talk model."""

    list_display = ("title", "speaker_name", "date_time", "room")
    list_filter = ("room", "date_time")
    search_fields = ("title", "speaker_name", "description")
    date_hierarchy = "date_time"
    ordering = ("date_time",)

    fieldsets = (
        (
            "Basic Information",
            {
                "fields": ("title", "speaker_name", "description"),
            },
        ),
        (
            "Schedule",
            {
                "fields": ("date_time", "room"),
            },
        ),
        (
            "Links",
            {
                "fields": ("pretalx_link", "video_link", "video_start_time"),
            },
        ),
        (
            "Media",
            {
                "fields": ("image",),
            },
        ),
    )
