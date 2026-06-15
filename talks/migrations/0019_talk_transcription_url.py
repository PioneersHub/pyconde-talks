from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0018_remove_questionvote_talks_quest_questio_99a23f_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="talk",
            name="transcription_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="Link to external transcription page for this talk.",
            ),
        ),
    ]
