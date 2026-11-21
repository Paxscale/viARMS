from django.db import migrations


def correct_announcement_title(apps, schema_editor):
    AdminNotification = apps.get_model('rms', 'AdminNotification')
    AdminNotification.objects.filter(title__iexact='Annuce').update(title='Announcement')


class Migration(migrations.Migration):

    dependencies = [
        ('rms', '0030_business_mode_and_receipt_footer'),
    ]

    operations = [
        migrations.RunPython(correct_announcement_title, migrations.RunPython.noop),
    ]
