from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0002_event_code_of_conduct_url_event_imprint_url_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="show_rating_summary",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "Show average rating and rating count to regular users. "
                    "Staff and superusers always see rating summaries."
                ),
            ),
        ),
    ]
