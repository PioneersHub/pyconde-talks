import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0004_event_transcriptions_url"),
        ("talks", "0023_pendingpretalxchange"),
    ]

    operations = [
        migrations.AddField(
            model_name="room",
            name="event",
            field=models.ForeignKey(
                blank=True,
                help_text="Event this room belongs to",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="rooms",
                to="events.event",
            ),
        ),
        migrations.AddField(
            model_name="room",
            name="pretalx_id",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Stable Pretalx room id (slot.room.id); null for manual/legacy rooms",
                null=True,
            ),
        ),
    ]
