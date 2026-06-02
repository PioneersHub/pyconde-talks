import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0004_event_transcriptions_url"),
        ("talks", "0028_backfill_talk_event"),
    ]

    operations = [
        migrations.AlterField(
            model_name="talk",
            name="event",
            field=models.ForeignKey(
                help_text="Event this talk belongs to",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="talks",
                to="events.event",
            ),
        ),
    ]
