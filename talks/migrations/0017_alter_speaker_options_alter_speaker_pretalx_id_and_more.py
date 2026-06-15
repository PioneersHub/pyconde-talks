from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0016_add_saved_talk"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="speaker",
            options={
                "ordering": ["name"],
                "verbose_name": "Speaker",
                "verbose_name_plural": "Speakers",
            },
        ),
        migrations.AlterField(
            model_name="speaker",
            name="pretalx_id",
            field=models.CharField(
                help_text="Unique identifier for the speaker in the Pretalx system",
                max_length=50,
                unique=True,
            ),
        ),
        migrations.AddIndex(
            model_name="speaker",
            index=models.Index(fields=["pretalx_id"], name="talks_speak_pretalx_b666dc_idx"),
        ),
    ]
