from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('talks', '0011_alter_questionvote_unique_together_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='talk',
            index=models.Index(fields=['room', 'start_time'], name='talks_talk_room_id_73a62a_idx'),
        ),
        migrations.AddIndex(
            model_name='talk',
            index=models.Index(fields=['hide', 'start_time'], name='talks_talk_hide_6bbaaf_idx'),
        ),
        migrations.AddIndex(
            model_name='talk',
            index=models.Index(fields=['presentation_type', 'start_time'], name='talks_talk_present_2dee92_idx'),
        ),
    ]
