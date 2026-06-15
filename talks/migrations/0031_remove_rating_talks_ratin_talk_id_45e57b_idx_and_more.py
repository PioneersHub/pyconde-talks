from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("talks", "0030_talk_session_chair"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="rating",
            name="talks_ratin_talk_id_45e57b_idx",
        ),
        migrations.RemoveIndex(
            model_name="savedtalk",
            name="talks_saved_user_id_a768c9_idx",
        ),
        migrations.RemoveIndex(
            model_name="speaker",
            name="talks_speak_pretalx_b666dc_idx",
        ),
    ]
