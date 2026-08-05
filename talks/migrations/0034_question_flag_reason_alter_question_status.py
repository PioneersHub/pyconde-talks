from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0033_alter_talk_hide"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="flag_reason",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Why this question was held for review, when the spam heuristics caught "
                    "it. Empty means it was not auto-flagged."
                ),
                max_length=100,
            ),
        ),
        migrations.AlterField(
            model_name="question",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending review"),
                    ("approved", "Approved"),
                    ("answered", "Answered"),
                    ("rejected", "Rejected"),
                ],
                default="approved",
                help_text="Status of the question",
                max_length=20,
            ),
        ),
    ]
