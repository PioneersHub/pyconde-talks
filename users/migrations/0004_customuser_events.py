from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0001_initial'),
        ('users', '0003_alter_customuser_date_joined'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='events',
            field=models.ManyToManyField(blank=True, help_text='Events the user has access to', related_name='users', to='events.event'),
        ),
    ]
