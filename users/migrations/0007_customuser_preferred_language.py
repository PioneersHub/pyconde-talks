from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0006_alter_customuser_display_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="preferred_language",
            field=models.CharField(
                blank=True,
                choices=[("en", "English"), ("pt-br", "Portuguese (Brazil)")],
                help_text="Language for the interface and emails. Leave blank to follow the browser or site default.",
                max_length=10,
                verbose_name="preferred language",
            ),
        ),
    ]
