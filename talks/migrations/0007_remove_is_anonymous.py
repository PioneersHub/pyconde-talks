from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0006_alter_question_is_anonymous"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="question",
            name="is_anonymous",
        ),
    ]
