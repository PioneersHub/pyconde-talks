import django.db.models.deletion
import talks.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0001_initial'),
        ('talks', '0013_rating'),
    ]

    operations = [
        migrations.AddField(
            model_name='talk',
            name='event',
            field=models.ForeignKey(blank=True, help_text='Event this talk belongs to', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='talks', to='events.event'),
        ),
        migrations.AlterField(
            model_name='talk',
            name='image',
            field=models.ImageField(blank=True, help_text='Image for the talk. Overrides the external image URL if provided.', null=True, upload_to=talks.models._talk_image_upload_path),
        ),
        migrations.AddIndex(
            model_name='talk',
            index=models.Index(fields=['event'], name='talks_talk_event_i_fe7310_idx'),
        ),
        migrations.AddIndex(
            model_name='talk',
            index=models.Index(fields=['event', 'start_time'], name='talks_talk_event_i_7ac28f_idx'),
        ),
    ]
