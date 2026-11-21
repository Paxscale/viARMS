from django.db import migrations


def normalize_titles(apps, schema_editor):
    AdminNotification = apps.get_model('rms', 'AdminNotification')
    AdminNotification.objects.filter(title__iexact='Annuce').update(title='Updates')
    AdminNotification.objects.filter(title__iexact='Announcement').update(title='Updates')


class Migration(migrations.Migration):

    dependencies = [
        ('rms', '0031_fix_announcement_title_typo'),
    ]

    operations = [
        migrations.RunPython(normalize_titles, migrations.RunPython.noop),
    ]
