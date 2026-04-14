from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0020_add_transcription_url_to_streaming"),
    ]

    operations = [
        migrations.AlterField(
            model_name="talk",
            name="presentation_type",
            field=models.CharField(
                choices=[
                    ("Keynote", "Keynote"),
                    ("Kids", "Kids"),
                    ("Lightning", "Lightning Talk"),
                    ("Open Space", "Open Space"),
                    ("Panel", "Panel"),
                    ("Plenary", "Plenary"),
                    ("Talk", "Talk"),
                    ("Tutorial", "Tutorial"),
                ],
                default="Talk",
                help_text="Type of the presentation",
                max_length=10,
            ),
        ),
    ]
