from django.db import migrations, models


def ratings_to_highlight(apps, schema_editor):
    """The 1-4 star rating becomes a single "highlight" mark: 3 stars and more count as highlight."""
    apps.get_model("bookings", "Picture").objects.filter(rating__gte=3).update(highlight=True)


class Migration(migrations.Migration):

    dependencies = [
        ('bookings', '0019_picture_archive_gallery_guest_details'),
    ]

    operations = [
        migrations.AddField(
            model_name='picture',
            name='highlight',
            field=models.BooleanField(default=False, verbose_name='Highlight'),
        ),
        migrations.RunPython(ratings_to_highlight, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='picture',
            name='rating',
        ),
    ]
