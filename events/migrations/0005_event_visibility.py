from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0004_event_transcriptions_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("hidden", "Hidden (login required for everything)"),
                    ("schedule_only", "Schedule only (recordings require login)"),
                    ("public", "Public (everything, including recordings)"),
                ],
                db_index=True,
                default="hidden",
                help_text=(
                    "What visitors can see without logging in. Hidden keeps the whole event "
                    "behind the login wall. Schedule only publishes titles, abstracts and "
                    "speakers but keeps recordings for ticket holders. Public opens recordings "
                    "too, and lets anyone register without a ticket check. Q&A and ratings "
                    "always require a login."
                ),
                max_length=20,
            ),
        ),
    ]
