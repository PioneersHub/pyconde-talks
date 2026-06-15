from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="code_of_conduct_url",
            field=models.URLField(
                blank=True, default="", help_text="URL to the event's Code of Conduct page"
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="imprint_url",
            field=models.URLField(
                blank=True, default="", help_text="URL to the event's Imprint page"
            ),
        ),
        migrations.AddField(
            model_name="event",
            name="privacy_policy_url",
            field=models.URLField(
                blank=True, default="", help_text="URL to the event's Privacy Policy page"
            ),
        ),
    ]
