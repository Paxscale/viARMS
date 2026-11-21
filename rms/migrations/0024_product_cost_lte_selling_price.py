from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rms', '0023_userprofile_tenant_type'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='product',
            constraint=models.CheckConstraint(
                check=models.Q(cost_price__lte=models.F('selling_price')),
                name='product_cost_lte_selling_price',
            ),
        ),
    ]
