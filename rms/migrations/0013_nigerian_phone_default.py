from django.db import migrations, models


def replace_legacy_phone_default(apps, schema_editor):
    StoreSettings = apps.get_model('rms', 'StoreSettings')
    StoreSettings.objects.filter(phone='+1 555-0199').update(phone='+234 810 026 2697')


def restore_legacy_phone_default(apps, schema_editor):
    StoreSettings = apps.get_model('rms', 'StoreSettings')
    StoreSettings.objects.filter(phone='+234 810 026 2697').update(phone='+1 555-0199')


class Migration(migrations.Migration):

    dependencies = [
        ('rms', '0012_product_image'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storesettings',
            name='phone',
            field=models.CharField(blank=True, default='+234 810 026 2697', max_length=50, null=True),
        ),
        migrations.RunPython(replace_legacy_phone_default, restore_legacy_phone_default),
    ]
