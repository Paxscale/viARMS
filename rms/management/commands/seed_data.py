from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from rms.models import Category, Product, StoreSettings, Shop, UserProfile, Sale
from decimal import Decimal
from django.utils import timezone
from datetime import timedelta

class Command(BaseCommand):
    help = 'Seeds database with a default shop, admin profile backfills, categories, products, and default store settings.'

    def handle(self, *args, **options):
        self.stdout.write('Seeding/Backfilling database components for Phase 2...')

        # 1. Create Default Shop
        default_shop, created = Shop.objects.get_or_create(
            name='viARMS Lagos Central',
            defaults={
                'location': 'Plot 104 Marina Boulevard, Lagos Island, Nigeria',
                'phone': '+234 1 555 0199'
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Default shop "{default_shop.name}" created.'))

        # 2. Create Default Superuser/Admin and link Profile
        admin_user, created = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@viarms.com',
                'is_staff': True,
                'is_superuser': True
            }
        )
        if created:
            admin_user.set_password('admin123')
            admin_user.save()
            self.stdout.write(self.style.SUCCESS('Superuser "admin" created with password "admin123".'))
        
        # Ensure Profile exists and is set to Admin
        admin_profile, created = UserProfile.objects.get_or_create(
            user=admin_user,
            defaults={'role': 'ADMIN', 'shop': default_shop}
        )
        if not created:
            admin_profile.role = 'ADMIN'
            if not admin_profile.shop:
                admin_profile.shop = default_shop
            admin_profile.save()
            self.stdout.write('Superuser profile verified.')

        # Ensure default shop manager (admin can act as manager or we create a manager)
        default_shop.manager = admin_user
        default_shop.save()

        # Ensure all existing user profiles are created and backfilled
        for user in User.objects.all():
            profile, created = UserProfile.objects.get_or_create(user=user)
            if not profile.shop:
                profile.shop = default_shop
                profile.save()

        # 3. Create Categories
        cats_to_create = [
            {'name': 'Beverages', 'description': 'Soda, juices, milk products, and energy drinks'},
            {'name': 'Snacks & Confectionery', 'description': 'Chips, candies, cookies, and chocolate bars'},
            {'name': 'Pantry Staples', 'description': 'Rice, flour, cooking oils, grains, and spices'},
            {'name': 'Hygiene & Toiletries', 'description': 'Soap, toothpaste, detergents, and body washes'},
        ]
        
        categories = {}
        for cat_data in cats_to_create:
            cat, created = Category.objects.get_or_create(
                name=cat_data['name'],
                defaults={'description': cat_data['description']}
            )
            categories[cat.name] = cat

        # 4. Create Products with Expiry Dates
        today = timezone.localdate()
        prods_to_create = [
            {
                'name': 'Coca-Cola 350ml Can',
                'sku': '5449000131805',
                'category': categories['Beverages'],
                'cost_price': Decimal('350.00'),
                'selling_price': Decimal('500.00'),
                'stock_quantity': 60,
                'min_stock_level': 10,
                'description': 'Classic Carbonated Soft Drink',
                'expiry_date': today + timedelta(days=180), # Safe
                'shop': default_shop
            },
            {
                'name': 'Potato Chips Classic 150g',
                'sku': '028400090896',
                'category': categories['Snacks & Confectionery'],
                'cost_price': Decimal('650.00'),
                'selling_price': Decimal('950.00'),
                'stock_quantity': 35,
                'min_stock_level': 8,
                'description': 'Crispy salted potato chips',
                'expiry_date': today + timedelta(days=15), # Expiring soon! (Within 30 days)
                'shop': default_shop
            },
            {
                'name': 'Basmati Rice 1kg Pack',
                'sku': '1234567890123',
                'category': categories['Pantry Staples'],
                'cost_price': Decimal('1200.00'),
                'selling_price': Decimal('1800.00'),
                'stock_quantity': 6,
                'min_stock_level': 8,
                'description': 'Premium long grain white rice',
                'expiry_date': today - timedelta(days=5), # Expired!
                'shop': default_shop
            },
            {
                'name': 'Milk Chocolate Bar 100g',
                'sku': '9876543210987',
                'category': categories['Snacks & Confectionery'],
                'cost_price': Decimal('450.00'),
                'selling_price': Decimal('700.00'),
                'stock_quantity': 45,
                'min_stock_level': 10,
                'description': 'Smooth and creamy milk chocolate',
                'expiry_date': today + timedelta(days=200),
                'shop': default_shop
            },
            {
                'name': 'Fluoride Toothpaste 120g',
                'sku': '4005900216447',
                'category': categories['Hygiene & Toiletries'],
                'cost_price': Decimal('750.00'),
                'selling_price': Decimal('1200.00'),
                'stock_quantity': 0,
                'min_stock_level': 5,
                'description': 'Fresh mint toothpaste with cavity protection',
                'expiry_date': today - timedelta(days=1), # Expired & Out of stock!
                'shop': default_shop
            },
            {
                'name': 'Antibacterial Hand Soap 250ml',
                'sku': '3014260271501',
                'category': categories['Hygiene & Toiletries'],
                'cost_price': Decimal('850.00'),
                'selling_price': Decimal('1400.00'),
                'stock_quantity': 18,
                'min_stock_level': 5,
                'description': 'Liquid handwash pump',
                'expiry_date': today + timedelta(days=365),
                'shop': default_shop
            },
            {
                'name': 'Pure Sunflower Cooking Oil 1L',
                'sku': '4820023456789',
                'category': categories['Pantry Staples'],
                'cost_price': Decimal('1800.00'),
                'selling_price': Decimal('2500.00'),
                'stock_quantity': 12,
                'min_stock_level': 5,
                'description': 'All-purpose refined cooking oil',
                'expiry_date': today + timedelta(days=25), # Expiring soon! (Within 30 days)
                'shop': default_shop
            }
        ]

        for p_data in prods_to_create:
            product, created = Product.objects.get_or_create(
                sku=p_data['sku'],
                defaults={
                    'name': p_data['name'],
                    'category': p_data['category'],
                    'cost_price': p_data['cost_price'],
                    'selling_price': p_data['selling_price'],
                    'stock_quantity': p_data['stock_quantity'],
                    'min_stock_level': p_data['min_stock_level'],
                    'description': p_data['description'],
                    'expiry_date': p_data['expiry_date'],
                    'shop': p_data['shop']
                }
            )
            if not created:
                # Update shop and expiry date for existing products
                product.shop = default_shop
                product.expiry_date = p_data['expiry_date']
                # Scale pricing to Nigerian Naira magnitudes (approximate numbers)
                product.cost_price = p_data['cost_price']
                product.selling_price = p_data['selling_price']
                product.save()

        # Backfill all products and sales that have no shop
        Product.objects.filter(shop__isnull=True).update(shop=default_shop)
        Sale.objects.filter(shop__isnull=True).update(shop=default_shop)

        # 5. Create/Update StoreSettings (15% tax)
        settings = StoreSettings.get_settings()
        settings.store_name = 'viARMS Retail Hub'
        settings.address = 'Marina Street, Lagos Island, Nigeria'
        settings.phone = '+234 1 555 0100'
        settings.tax_rate = Decimal('7.50') # Standard Nigerian VAT is 7.5%
        settings.receipt_footer = 'Thank you for shopping with viARMS Lagos! Please retain your receipt for returns.'
        settings.save()

        self.stdout.write(self.style.SUCCESS('Database seeding and backfilling finished successfully!'))
