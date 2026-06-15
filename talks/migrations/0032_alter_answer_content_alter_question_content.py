from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0031_remove_rating_talks_ratin_talk_id_45e57b_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="answer",
            name="content",
            field=models.TextField(help_text="The answer text", max_length=2000),
        ),
        migrations.AlterField(
            model_name="question",
            name="content",
            field=models.TextField(help_text="The question text", max_length=2000),
        ),
    ]
