from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0017_alter_speaker_options_alter_speaker_pretalx_id_and_more"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="questionvote",
            name="talks_quest_questio_99a23f_idx",
        ),
    ]
