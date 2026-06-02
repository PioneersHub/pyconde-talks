from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0004_event_transcriptions_url"),
        ("talks", "0025_backfill_room_event"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="room",
            options={
                "ordering": ["event", "name"],
                "verbose_name": "Room",
                "verbose_name_plural": "Rooms",
            },
        ),
        migrations.AlterField(
            model_name="room",
            name="name",
            field=models.CharField(help_text="Name of the room", max_length=50),
        ),
        migrations.AddConstraint(
            model_name="room",
            constraint=models.UniqueConstraint(
                fields=("event", "name"), name="uniq_room_event_name"
            ),
        ),
        migrations.AddConstraint(
            model_name="room",
            constraint=models.UniqueConstraint(
                condition=models.Q(("pretalx_id__isnull", False)),
                fields=("event", "pretalx_id"),
                name="uniq_room_event_pretalx_id",
            ),
        ),
    ]
