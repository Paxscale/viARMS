from django.db import migrations, models
import django.db.models.deletion


def create_tenant_store_settings(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('rms', 'UserProfile')
    Shop = apps.get_model('rms', 'Shop')
    StoreSettings = apps.get_model('rms', 'StoreSettings')

    tenant_owner_ids = UserProfile.objects.exclude(
        tenant_owner_id__isnull=True
    ).values_list('tenant_owner_id', flat=True).distinct()
    for owner_id in tenant_owner_ids.iterator():
        try:
            owner = User.objects.get(pk=owner_id, is_superuser=False)
        except User.DoesNotExist:
            continue
        shop = Shop.objects.filter(tenant_owner_id=owner_id).order_by('id').first()
        StoreSettings.objects.get_or_create(
            tenant_owner_id=owner_id,
            defaults={
                'store_name': shop.name if shop else owner.username,
                'address': (shop.location or '') if shop else '',
                'phone': (shop.phone or '') if shop else '',
                'email': owner.email or '',
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('rms', '0033_boutique_business_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='storesettings',
            name='tenant_owner',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='store_settings',
                to='auth.user',
            ),
        ),
        migrations.RunPython(create_tenant_store_settings, migrations.RunPython.noop),
    ]
