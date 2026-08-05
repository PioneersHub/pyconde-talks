from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0005_event_visibility"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="qa_mode",
            field=models.CharField(
                choices=[
                    ("open", "Open (questions appear immediately)"),
                    ("moderated", "Moderated (questions wait for approval)"),
                    ("frozen", "Frozen (existing questions stay, no new ones)"),
                    ("disabled", "Disabled (Q&A hidden entirely)"),
                ],
                default="open",
                help_text=(
                    "Whether attendees can post questions, and whether new ones appear "
                    "immediately or wait for a moderator. Freeze or disable the Q&A once an "
                    "event is over and nobody is watching the queue any more."
                ),
                max_length=10,
                verbose_name="Q&A mode",
            ),
        ),
    ]
