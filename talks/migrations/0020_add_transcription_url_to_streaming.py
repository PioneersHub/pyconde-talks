from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0019_talk_transcription_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="streaming",
            name="transcription_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Link to the transcription page for talks in this streaming session.",
            ),
        ),
    ]
