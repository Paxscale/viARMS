from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('rms', '0013_nigerian_phone_default')]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='business_type',
            field=models.CharField(choices=[
                ('GENERAL', 'General Retail / Provision Store'),
                ('PHARMACEUTICAL', 'Pharmaceuticals'),
                ('BOOK_ACCESSORIES', 'Bookshop & Accessories'),
                ('UTENSILS', 'Utensils & Housewares'),
                ('LAUNDRY', 'Laundry & Dry Cleaning'),
            ], default='GENERAL', max_length=30),
        ),
    ]
