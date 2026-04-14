from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0003_event_show_rating_summary"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="transcriptions_url",
            field=models.URLField(
                blank=True, default="", help_text="URL to the transcriptions overview page"
            ),
        ),
    ]
