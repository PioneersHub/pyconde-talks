from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0032_alter_answer_content_alter_question_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="talk",
            name="hide",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Hide this talk from everyone except administrators, whatever the event's "
                    "visibility and even for ticket holders. Use it for an embargoed or "
                    "cancelled session."
                ),
            ),
        ),
    ]
