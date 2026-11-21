from django.db import migrations, models


def rename_laundry_type(apps, schema_editor):
    UserProfile = apps.get_model('rms', 'UserProfile')
    UserProfile.objects.filter(business_type='LAUNDRY').update(business_type='BOUTIQUE')


def restore_laundry_type(apps, schema_editor):
    UserProfile = apps.get_model('rms', 'UserProfile')
    UserProfile.objects.filter(business_type='BOUTIQUE').update(business_type='LAUNDRY')


class Migration(migrations.Migration):

    dependencies = [
        ('rms', '0032_normalize_update_notification_titles'),
    ]

    operations = [
        migrations.RunPython(rename_laundry_type, restore_laundry_type),
        migrations.AlterField(
            model_name='userprofile',
            name='business_type',
            field=models.CharField(
                choices=[
                    ('GENERAL', 'General Retail / Provision Store'),
                    ('PHARMACEUTICAL', 'Pharmaceuticals'),
                    ('BOOK_ACCESSORIES', 'Bookshop & Accessories'),
                    ('UTENSILS', 'Utensils & Housewares'),
                    ('BOUTIQUE', 'Boutique'),
                ],
                default='GENERAL',
                max_length=30,
            ),
        ),
    ]
