import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Display name of the event. Include the year if applicable.', max_length=200, unique=True)),
                ('slug', models.SlugField(help_text='Event slug. Name used in URLs and for assets/media organization. Not necessarily the same as the Pretalx slug.', max_length=100, unique=True)),
                ('year', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(2000), django.core.validators.MaxValueValidator(2100)], verbose_name='Event year')),
                ('validation_api_url', models.URLField(blank=True, default='', help_text='URL of the external API used to validate if a user bought a ticket for this event. Leave blank to disable API validation for this event.')),
                ('is_active', models.BooleanField(default=True, help_text='Whether this event is currently active and visible on the site')),
                ('main_website_url', models.URLField(blank=True, default='', help_text='Main website URL for the event')),
                ('venue_url', models.URLField(blank=True, default='', help_text='Venue information URL')),
                ('logo_svg_name', models.CharField(blank=True, default='', help_text='Name of the SVG logo file (without extension)', max_length=200)),
                ('made_by_name', models.CharField(blank=True, default='', help_text='Name of the organizing team or community', max_length=200)),
                ('made_by_url', models.URLField(blank=True, default='', help_text='URL linking to the organizer or community page')),
                ('pretalx_url', models.URLField(blank=True, default='', help_text="Pretalx event base URL (e.g., 'https://pretalx.com/my-event')")),
            ],
            options={
                'verbose_name': 'Event',
                'verbose_name_plural': 'Events',
                'ordering': ['name'],
            },
        ),
    ]
