from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Drop the index on ``Event.visibility``.

    A three-value column on a table with one row per conference: the planner reads the whole table
    faster either way, so the index only ever cost write time and disk. Done as its own migration
    rather than by editing 0005, which has already been applied on developer databases.
    """

    dependencies = [
        ("events", "0006_event_qa_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="visibility",
            field=models.CharField(
                choices=[
                    ("hidden", "Hidden (login required for everything)"),
                    ("schedule_only", "Schedule only (recordings require login)"),
                    ("public", "Public (everything, including recordings)"),
                ],
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
