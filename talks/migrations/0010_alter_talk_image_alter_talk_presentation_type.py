from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('talks', '0009_alter_question_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='talk',
            name='image',
            field=models.ImageField(blank=True, help_text='Image for the talk. Overrides the external image URL if provided.', null=True, upload_to='talk_images/videos.pydata-berlin.org/'),
        ),
        migrations.AlterField(
            model_name='talk',
            name='presentation_type',
            field=models.CharField(choices=[('Keynote', 'Keynote'), ('Kids', 'Kids'), ('Lightning', 'Lightning Talk'), ('Panel', 'Panel'), ('Plenary', 'Plenary'), ('Talk', 'Talk'), ('Tutorial', 'Tutorial')], default='Talk', help_text='Type of the presentation', max_length=10),
        ),
    ]
