import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0004_event_transcriptions_url"),
        ("talks", "0026_room_event_scoped_constraints"),
    ]

    operations = [
        migrations.AlterField(
            model_name="room",
            name="event",
            field=models.ForeignKey(
                help_text="Event this room belongs to",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="rooms",
                to="events.event",
            ),
        ),
    ]
