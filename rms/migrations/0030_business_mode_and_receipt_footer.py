from django.db import migrations, models


DEFAULT_RECEIPT_FOOTER = (
    'Thank you for shopping with viARMS Record Management System. '
    'Powered by Scales Technologies.'
)
OLD_DEFAULT_RECEIPT_FOOTER = 'Thank you for shopping with us! Please come again.'


def update_default_receipt_footer(apps, schema_editor):
    StoreSettings = apps.get_model('rms', 'StoreSettings')
    StoreSettings.objects.filter(
        models.Q(receipt_footer__isnull=True)
        | models.Q(receipt_footer='')
        | models.Q(receipt_footer=OLD_DEFAULT_RECEIPT_FOOTER)
    ).update(receipt_footer=DEFAULT_RECEIPT_FOOTER)


class Migration(migrations.Migration):

    dependencies = [
        ('rms', '0029_accountappeal'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='tenant_type',
            field=models.CharField(
                choices=[
                    ('RETAIL', 'Retail'),
                    ('WHOLESALE', 'Wholesale'),
                    ('RETAIL_WHOLESALE', 'Retail and Wholesale'),
                ],
                default='RETAIL',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='storesettings',
            name='receipt_footer',
            field=models.TextField(
                blank=True,
                null=True,
                default=DEFAULT_RECEIPT_FOOTER,
            ),
        ),
        migrations.RunPython(update_default_receipt_footer, migrations.RunPython.noop),
    ]
