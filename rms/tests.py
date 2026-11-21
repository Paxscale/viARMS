from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.utils import timezone
from datetime import timedelta, date, datetime
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from PIL import Image
from rms.models import Category, Product, Sale, SaleItem, Shop, StoreSettings, AdminNotification
import json
from urllib.parse import urlsplit

class RMSModelsAndAPITests(TestCase):

    def setUp(self):
        # Create users
        self.admin_user = User.objects.create_superuser('admin', 'admin@test.com', 'admin123')
        self.cashier_user = User.objects.create_user('cashier', 'cashier@test.com', 'cashier123')
        self.store_admin_user = User.objects.create_user('store_admin', password='admin123')

        self.shop = Shop.objects.create(name="Test Retail")
        cashier_profile = self.cashier_user.profile
        cashier_profile.shop = self.shop
        cashier_profile.is_approved = True
        cashier_profile.save()
        store_admin_profile = self.store_admin_user.profile
        store_admin_profile.role = 'ADMIN'
        store_admin_profile.is_approved = True
        store_admin_profile.save()

        # Create settings
        self.settings = StoreSettings.get_settings()
        self.settings.store_name = "Test Retail"
        self.settings.tax_rate = Decimal('10.00')
        self.settings.save()

        # Create Category
        self.category = Category.objects.create(name="Groceries", description="Daily groceries")

        # Create Products
        self.product_coke = Product.objects.create(
            name="Coke 300ml",
            sku="11111",
            category=self.category,
            shop=self.shop,
            cost_price=Decimal('0.50'),
            selling_price=Decimal('1.20'),
            stock_quantity=20,
            min_stock_level=5
        )

        self.product_chips = Product.objects.create(
            name="Chips 100g",
            sku="22222",
            category=self.category,
            shop=self.shop,
            cost_price=Decimal('0.80'),
            selling_price=Decimal('2.00'),
            stock_quantity=10,
            min_stock_level=2
        )

        # Setup HTTP Client
        self.client = Client()

    def create_tenant(self, username, approved=True):
        owner = User.objects.create_user(username, password='tenant123')
        profile = owner.profile
        profile.role = 'ADMIN'
        profile.tenant_owner = owner
        profile.is_approved = approved
        profile.save()
        shop = Shop.objects.create(name=f'{username} Shop', tenant_owner=owner)
        profile.shop = shop
        profile.save()
        return owner, profile, shop

    def verify_signup_email(self, email, first_name='Test', last_name='User'):
        from django.core import mail

        mail.outbox = []
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            response = self.client.post(reverse('register'), {
                'stage': 'send_verification',
                'email': email,
                'first_name': first_name,
                'last_name': last_name,
            })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mail.outbox)
        verification_line = next(
            line for line in mail.outbox[-1].body.splitlines()
            if line.startswith('Verify your email: ')
        )
        verification_url = verification_line.partition(': ')[2].strip()
        verification_response = self.client.get(urlsplit(verification_url).path)
        self.assertEqual(verification_response.status_code, 302)
        return self.client.get(reverse('register'))

    def test_super_admin_can_suspend_and_reactivate_tenant_access(self):
        owner, profile, shop = self.create_tenant('managed_tenant')
        staff = User.objects.create_user('managed_cashier', password='cashier123')
        staff_profile = staff.profile
        staff_profile.tenant_owner = owner
        staff_profile.shop = shop
        staff_profile.is_approved = True
        staff_profile.save()

        staff_client = Client()
        self.assertTrue(staff_client.login(username='managed_cashier', password='cashier123'))
        self.assertEqual(staff_client.get(reverse('dashboard')).status_code, 200)

        self.client.login(username='admin', password='admin123')
        response = self.client.post(reverse('admin_panel'), {
            'action': 'suspend_tenant',
            'user_id': owner.id,
        })
        self.assertEqual(response.status_code, 302)
        owner.refresh_from_db()
        self.assertFalse(owner.is_active)
        self.assertEqual(staff_client.get(reverse('dashboard')).status_code, 302)

        response = self.client.post(reverse('admin_panel'), {
            'action': 'activate_tenant',
            'user_id': owner.id,
        })
        self.assertEqual(response.status_code, 302)
        owner.refresh_from_db()
        self.assertTrue(owner.is_active)
        self.assertTrue(staff_client.login(username='managed_cashier', password='cashier123'))
        self.assertEqual(staff_client.get(reverse('dashboard')).status_code, 200)

    def test_tenant_deletion_requires_username_and_removes_business_records(self):
        owner, profile, shop = self.create_tenant('delete_me')
        staff = User.objects.create_user('delete_me_cashier', password='cashier123')
        staff_profile = staff.profile
        staff_profile.tenant_owner = owner
        staff_profile.shop = shop
        staff_profile.save()
        product = Product.objects.create(
            name='Tenant product',
            sku='TENANT-001',
            cost_price=Decimal('1.00'),
            selling_price=Decimal('2.00'),
            stock_quantity=3,
            shop=shop,
        )
        self.client.login(username='admin', password='admin123')

        response = self.client.post(reverse('admin_panel'), {
            'action': 'delete_tenant',
            'user_id': owner.id,
            'delete_confirmation': 'wrong-name',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(id=owner.id).exists())
        self.assertTrue(Product.objects.filter(id=product.id).exists())

        response = self.client.post(reverse('admin_panel'), {
            'action': 'delete_tenant',
            'user_id': owner.id,
            'delete_confirmation': owner.username,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(id=owner.id).exists())
        self.assertFalse(User.objects.filter(id=staff.id).exists())
        self.assertFalse(Shop.objects.filter(id=shop.id).exists())
        self.assertFalse(Product.objects.filter(id=product.id).exists())

    def test_admin_panel_search_and_tenant_status_summary(self):
        active_owner, _, _ = self.create_tenant('active_tenant')
        self.create_tenant('pending_tenant', approved=False)
        suspended_owner, _, _ = self.create_tenant('suspended_tenant')
        suspended_owner.is_active = False
        suspended_owner.save(update_fields=['is_active'])
        self.client.login(username='admin', password='admin123')

        response = self.client.get(reverse('admin_panel'), {'tenant_search': 'active_tenant'})
        self.assertEqual(
            [tenant.user.username for tenant in response.context['all_tenants']],
            ['active_tenant'],
        )
        self.assertEqual(response.context['active_tenant_count'], 1)
        self.assertEqual(response.context['pending_tenant_count'], 1)
        self.assertEqual(response.context['suspended_tenant_count'], 1)

    def test_model_properties(self):
        # Test low stock alert
        self.product_chips.stock_quantity = 2
        self.assertTrue(self.product_chips.is_low_stock)

        self.product_chips.stock_quantity = 5
        self.assertFalse(self.product_chips.is_low_stock)

        # Test profit margin property
        self.assertEqual(self.product_chips.profit_margin, Decimal('1.20'))

    def test_utensil_product_material_is_saved(self):
        registration_response = self.verify_signup_email('utensils-signup@test.com')
        self.assertContains(registration_response, 'value="UTENSILS"')

        utensil_admin = User.objects.create_user('utensil_admin', password='admin123')
        profile = utensil_admin.profile
        profile.role = 'ADMIN'
        profile.tenant_owner = utensil_admin
        profile.business_type = 'UTENSILS'
        profile.is_approved = True
        profile.save()
        shop = Shop.objects.create(name="Utensils", tenant_owner=utensil_admin)
        self.client.login(username='utensil_admin', password='admin123')

        response = self.client.post(reverse('products'), {
            'action': 'add_product',
            'shop_id': shop.id,
            'name': 'Ceramic Plate',
            'sku': 'PLATE-001',
            'cost_price': '2.00',
            'selling_price': '4.00',
            'stock_quantity': '10',
            'min_stock_level': '2',
            'material': 'CERAMIC',
        })
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(sku='PLATE-001')
        self.assertEqual(product.material, 'CERAMIC')
        self.assertEqual(product.get_material_display(), 'Ceramic')

        response = self.client.post(reverse('products'), {
            'action': 'edit_product',
            'product_id': product.id,
            'shop_id': shop.id,
            'name': product.name,
            'sku': product.sku,
            'cost_price': '2.00',
            'selling_price': '4.00',
            'stock_quantity': '10',
            'min_stock_level': '2',
            'material': 'FABRIC',
        })
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.get_material_display(), 'Fabric')

        response = self.client.get(reverse('products'))
        self.assertContains(response, 'Utensil Details')
        self.assertContains(response, 'Ceramic')
        self.assertContains(response, 'Fabric')
        self.assertContains(response, 'Plastic')
        self.assertContains(response, 'Fiber')
        self.assertContains(response, 'Other')

    def test_plastic_product_image_upload_and_no_expiry(self):
        utensil_admin = User.objects.create_user('plastic_admin', password='admin123')
        profile = utensil_admin.profile
        profile.role = 'ADMIN'
        profile.tenant_owner = utensil_admin
        profile.business_type = 'UTENSILS'
        profile.is_approved = True
        shop = Shop.objects.create(name="Plastic Goods", tenant_owner=utensil_admin)
        profile.shop = shop
        profile.save()
        self.client.login(username='plastic_admin', password='admin123')

        image_buffer = BytesIO()
        Image.new('RGB', (2, 2), color='blue').save(image_buffer, format='PNG')
        image_buffer.seek(0)
        image_upload = SimpleUploadedFile(
            'plastic-chair.png',
            image_buffer.read(),
            content_type='image/png',
        )

        with TemporaryDirectory() as media_dir, override_settings(MEDIA_ROOT=media_dir):
            response = self.client.post(reverse('products'), {
                'action': 'add_product',
                'shop_id': shop.id,
                'name': 'Plastic Chair',
                'sku': 'CHAIR-001',
                'cost_price': '10.00',
                'selling_price': '20.00',
                'stock_quantity': '5',
                'min_stock_level': '1',
                'material': 'PLASTIC',
                'expiry_date': '2027-01-01',
                'image': image_upload,
            })
            self.assertEqual(response.status_code, 302)

            product = Product.objects.get(sku='CHAIR-001')
            self.assertIsNone(product.expiry_date)
            self.assertTrue(product.image.name.startswith('products/'))
            self.assertTrue(product.image.name.endswith('.jpg'))
            self.assertTrue(product.image.storage.exists(product.image.name))
            self.assertLessEqual(product.image.size, 50 * 1024)
            with Image.open(product.image) as saved_image:
                self.assertEqual(saved_image.format, 'JPEG')
            product.image.close()

            response = self.client.get(reverse('products'))
            self.assertContains(response, product.image.url)
            self.assertContains(response, 'No Expiry')
            self.assertContains(response, 'id="prod-image-camera-button"')
            self.assertContains(response, 'id="product-image-camera-video"')
            self.assertContains(response, 'id="prod-image"')

            response = self.client.get(reverse('pos'))
            self.assertContains(response, product.image.url)
            self.assertContains(response, 'js/pos.js?v=')
            self.assertContains(response, 'id="pos-scan-button"')
            self.assertContains(response, 'id="pos-barcode-scanner-modal"')

    def test_bulk_product_form_creates_products_and_compresses_images(self):
        owner = User.objects.create_user('bulk_products_admin', password='admin123')
        profile = owner.profile
        profile.role = 'ADMIN'
        profile.tenant_owner = owner
        profile.business_type = 'UTENSILS'
        profile.is_approved = True
        shop = Shop.objects.create(name='Bulk Inventory Shop', tenant_owner=owner)
        profile.shop = shop
        profile.save()
        category = Category.objects.create(name='Cups', tenant_owner=owner)
        self.client.login(username=owner.username, password='admin123')

        image_buffer = BytesIO()
        Image.effect_noise((1024, 768), 100).convert('RGB').save(image_buffer, format='PNG')
        image_upload = SimpleUploadedFile(
            'cup.png',
            image_buffer.getvalue(),
            content_type='image/png',
        )

        with TemporaryDirectory() as media_dir, override_settings(MEDIA_ROOT=media_dir):
            page_response = self.client.get(reverse('products'))
            self.assertContains(page_response, 'id="bulk-products-modal"')
            self.assertContains(page_response, 'id="bulk-product-row-template"')
            self.assertContains(page_response, 'product-__index__-image')

            response = self.client.post(reverse('products'), {
                'action': 'bulk_add_products',
                'product_count': '2',
                'bulk_shop_id': str(shop.id),
                'product-0-name': 'Plastic Cup',
                'product-0-sku': 'CUP-001',
                'product-0-category_id': str(category.id),
                'product-0-cost_price': '20.00',
                'product-0-selling_price': '50.00',
                'product-0-stock_quantity': '12',
                'product-0-min_stock_level': '2',
                'product-0-expiry_date': 'not-a-date',
                'product-0-material': 'PLASTIC',
                'product-1-name': 'Ceramic Cup',
                'product-1-sku': 'CUP-002',
                'product-1-category_id': str(category.id),
                'product-1-cost_price': '30.00',
                'product-1-selling_price': '70.00',
                'product-1-stock_quantity': '8',
                'product-1-min_stock_level': '2',
                'product-1-material': 'CERAMIC',
                'product-0-image': image_upload,
            })

            self.assertEqual(response.status_code, 302)
            plastic_cup = Product.objects.get(sku='CUP-001')
            ceramic_cup = Product.objects.get(sku='CUP-002')
            self.assertEqual(plastic_cup.shop, shop)
            self.assertEqual(plastic_cup.category, category)
            self.assertIsNone(plastic_cup.expiry_date)
            self.assertEqual(plastic_cup.material, 'PLASTIC')
            self.assertTrue(plastic_cup.image.name.endswith('.jpg'))
            self.assertLessEqual(plastic_cup.image.size, 50 * 1024)
            self.assertFalse(ceramic_cup.image)
            with Image.open(plastic_cup.image) as compressed_image:
                self.assertLess(compressed_image.width, 1024)
            plastic_cup.image.close()

    def test_bulk_product_form_does_not_partially_create_invalid_rows(self):
        owner, profile, shop = self.create_tenant('bulk_invalid_admin')
        self.client.login(username=owner.username, password='tenant123')

        response = self.client.post(reverse('products'), {
            'action': 'bulk_add_products',
            'product_count': '2',
            'bulk_shop_id': str(shop.id),
            'product-0-name': 'Valid product',
            'product-0-sku': 'BULK-VALID',
            'product-0-cost_price': '2.00',
            'product-0-selling_price': '3.00',
            'product-0-stock_quantity': '4',
            'product-0-min_stock_level': '1',
            'product-1-name': 'Invalid product',
            'product-1-sku': 'BULK-INVALID',
            'product-1-cost_price': 'not-a-price',
            'product-1-selling_price': '3.00',
            'product-1-stock_quantity': '4',
            'product-1-min_stock_level': '1',
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Product.objects.filter(sku__startswith='BULK-').exists())

    def test_pos_and_dashboard_upgrades_are_available_for_every_business_type(self):
        business_types = ['GENERAL', 'PHARMACEUTICAL', 'BOOK_ACCESSORIES', 'UTENSILS']
        image_buffer = BytesIO()
        Image.new('RGB', (2, 2), color='green').save(image_buffer, format='PNG')
        image_content = image_buffer.getvalue()

        with TemporaryDirectory() as media_dir, override_settings(MEDIA_ROOT=media_dir):
            for index, business_type in enumerate(business_types):
                owner = User.objects.create_user(f'catalog_owner_{index}', password='admin123')
                profile = owner.profile
                profile.role = 'ADMIN'
                profile.tenant_owner = owner
                profile.business_type = business_type
                profile.is_approved = True
                shop = Shop.objects.create(name=f'Shop {index}', tenant_owner=owner)
                profile.shop = shop
                profile.save()
                self.client.login(username=owner.username, password='admin123')

                product = Product.objects.create(
                    name=f'Product {business_type}',
                    sku=f'SKU-{index}',
                    cost_price=Decimal('1.00'),
                    selling_price=Decimal('2.00'),
                    stock_quantity=5,
                    shop=shop,
                    image=SimpleUploadedFile(f'product-{index}.png', image_content, content_type='image/png'),
                    material='CERAMIC' if business_type == 'UTENSILS' else '',
                    author='Sample author' if business_type == 'BOOK_ACCESSORIES' else '',
                    generic_name='Sample ingredient' if business_type == 'PHARMACEUTICAL' else '',
                    dosage_form='Tablet' if business_type == 'PHARMACEUTICAL' else '',
                    strength='500 mg' if business_type == 'PHARMACEUTICAL' else '',
                )

                pos_response = self.client.get(reverse('pos'))
                self.assertContains(pos_response, 'id="pos-scan-button"')
                self.assertContains(pos_response, 'id="pos-barcode-scanner-modal"')
                self.assertContains(pos_response, product.image.url)
                if business_type == 'UTENSILS':
                    self.assertContains(pos_response, 'material_display')
                elif business_type == 'BOOK_ACCESSORIES':
                    self.assertContains(pos_response, 'Sample author')
                elif business_type == 'PHARMACEUTICAL':
                    self.assertContains(pos_response, 'Sample ingredient')

                dashboard_response = self.client.get(reverse('dashboard'))
                self.assertContains(dashboard_response, product.image.url)
                self.assertContains(dashboard_response, product.name)
                self.client.logout()

    def test_bookshop_accessories_business_has_both_product_field_sets(self):
        registration_response = self.verify_signup_email('bookshop-signup@test.com')
        self.assertContains(registration_response, 'value="BOOK_ACCESSORIES"')
        self.assertContains(registration_response, 'class="signup-shell"')
        self.assertContains(registration_response, 'class="glass-panel signup-card"')
        self.assertContains(registration_response, '@media (max-width: 576px)')

        business_owner = User.objects.create_user('book_accessories_owner', password='admin123')
        profile = business_owner.profile
        profile.role = 'ADMIN'
        profile.tenant_owner = business_owner
        profile.business_type = 'BOOK_ACCESSORIES'
        profile.is_approved = True
        profile.save()
        shop = Shop.objects.create(name="Books and Accessories", tenant_owner=business_owner)
        profile.shop = shop
        profile.save()
        self.client.login(username='book_accessories_owner', password='admin123')

        response = self.client.post(reverse('products'), {
            'action': 'add_product',
            'shop_id': shop.id,
            'name': 'Reference Book',
            'sku': 'BOOK-001',
            'cost_price': '5.00',
            'selling_price': '10.00',
            'stock_quantity': '8',
            'min_stock_level': '2',
            'author': 'A. Writer',
            'publisher': 'Example Press',
            'isbn': '978-0000000001',
            'color': 'Blue',
            'size': 'Large',
            'warranty_period': '1 Year',
        })
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(sku='BOOK-001')
        self.assertEqual(product.author, 'A. Writer')
        self.assertEqual(product.isbn, '978-0000000001')
        self.assertEqual(product.color, 'Blue')
        self.assertEqual(product.warranty_period, '1 Year')

        response = self.client.get(reverse('products'))
        self.assertContains(response, 'Book & Accessory Details')
        self.assertContains(response, 'Author')
        self.assertContains(response, 'Color')
        self.assertContains(response, 'Blue')

        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'Bookshop & Accessories Inventory')
        self.assertContains(response, 'Reference Book')
        self.assertContains(response, '978-0000000001')
        self.assertContains(response, 'Blue')

    def test_successful_checkout_api(self):
        # Log in as cashier
        self.client.login(username='cashier', password='cashier123')
        
        url = reverse('checkout_api')
        payload = {
            'items': [
                {'product_id': self.product_coke.id, 'quantity': 5},
                {'product_id': self.product_chips.id, 'quantity': 2}
            ],
            'discount': 1.00,
            'payment_method': 'CASH'
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        
        # Verify receipt data
        receipt = data['receipt']
        self.assertEqual(receipt['store_name'], "Test Retail")
        self.assertEqual(receipt['cashier'], "cashier")
        self.assertEqual(receipt['payment_method'], "Cash")
        
        # Subtotal: (1.20 * 5) + (2.00 * 2) = 6.00 + 4.00 = 10.00
        # Discount: 1.00
        # Taxable subtotal: 9.00
        # Tax (10%): 0.90
        # Total amount: 9.90
        self.assertEqual(receipt['subtotal'], "10.00")
        self.assertEqual(receipt['discount'], "1.00")
        self.assertEqual(receipt['tax_amount'], "0.90")
        self.assertEqual(receipt['total_amount'], "9.90")

        # Verify stock decrement
        self.product_coke.refresh_from_db()
        self.product_chips.refresh_from_db()
        self.assertEqual(self.product_coke.stock_quantity, 15)
        self.assertEqual(self.product_chips.stock_quantity, 8)

        # Verify database logs
        sale = Sale.objects.get(receipt_number=receipt['receipt_number'])
        self.assertEqual(sale.total_amount, Decimal('9.90'))
        self.assertEqual(sale.items.count(), 2)

    def test_failed_checkout_insufficient_stock(self):
        self.client.login(username='cashier', password='cashier123')
        
        url = reverse('checkout_api')
        # Coke stock is 20, we request 25
        payload = {
            'items': [
                {'product_id': self.product_coke.id, 'quantity': 25}
            ],
            'discount': 0.00,
            'payment_method': 'CASH'
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn("Insufficient stock", data['error'])

        # Stock should not be decremented
        self.product_coke.refresh_from_db()
        self.assertEqual(self.product_coke.stock_quantity, 20)

    def test_access_permissions_cashier_vs_admin(self):
        # 1. Cashier should NOT be able to view Settings page
        self.client.login(username='cashier', password='cashier123')
        response = self.client.get(reverse('settings'))
        # redirects to login because cashier is not admin (user_passes_test redirects to login by default if it fails)
        self.assertEqual(response.status_code, 302)

        # 2. Admin SHOULD be able to view Settings page
        self.client.login(username='store_admin', password='admin123')
        response = self.client.get(reverse('settings'))
        self.assertEqual(response.status_code, 200)
        
        # 3. Cashier can see dashboard
        self.client.login(username='cashier', password='cashier123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        # Verify cashier dashboard doesn't contain net profit string (it's hidden in the template)
        self.assertNotContains(response, "Today's Net Profit")

        # 4. Admin dashboard contains net profit
        self.client.login(username='store_admin', password='admin123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Today's Net Profit")

    def test_phone_fields_use_nigerian_default(self):
        self.assertEqual(self.settings.phone, "+234 810 026 2697")

        self.client.login(username='store_admin', password='admin123')
        settings_response = self.client.get(reverse('settings'))
        self.assertContains(settings_response, 'type="tel"')
        self.assertContains(settings_response, 'placeholder="+234 810 026 2697"')

        shops_response = self.client.get(reverse('shops'))
        self.assertContains(shops_response, 'type="tel"')
        self.assertContains(shops_response, 'placeholder="+234 810 026 2697"')


    def test_settings_and_receipt_data_fetching(self):
        # Update settings
        self.settings.store_name = "Global Supermart"
        self.settings.address = "45 Commercial Road, Victoria Island, Lagos"
        self.settings.phone = "+234 809 111 2233"
        self.settings.email = "support@globalsupermart.com"
        self.settings.receipt_footer = "Thanks for shopping! Valid for exchange in 7 days."
        self.settings.save()

        # Perform checkout as cashier
        self.client.login(username='cashier', password='cashier123')
        payload = {
            'items': [{'product_id': self.product_coke.id, 'quantity': 2}],
            'discount': 0.00,
            'payment_method': 'CASH'
        }
        checkout_res = self.client.post(
            reverse('checkout_api'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(checkout_res.status_code, 200)
        checkout_data = checkout_res.json()
        self.assertTrue(checkout_data['success'])
        receipt = checkout_data['receipt']
        self.assertEqual(receipt['store_name'], "Global Supermart")
        self.assertEqual(receipt['store_address'], "45 Commercial Road, Victoria Island, Lagos")
        self.assertEqual(receipt['store_phone'], "+234 809 111 2233")
        self.assertEqual(receipt['store_email'], "support@globalsupermart.com")
        self.assertEqual(receipt['receipt_footer'], "Thanks for shopping! Valid for exchange in 7 days.")

        # Test reprint endpoint sale_detail_json
        sale = Sale.objects.get(receipt_number=receipt['receipt_number'])
        reprint_res = self.client.get(reverse('sale_detail_json', kwargs={'sale_id': sale.id}))
        self.assertEqual(reprint_res.status_code, 200)
        reprint_data = reprint_res.json()
        self.assertTrue(reprint_data['success'])
        reprint_receipt = reprint_data['receipt']
        self.assertEqual(reprint_receipt['store_name'], "Global Supermart")
        self.assertEqual(reprint_receipt['store_address'], "45 Commercial Road, Victoria Island, Lagos")
        self.assertEqual(reprint_receipt['store_phone'], "+234 809 111 2233")
        self.assertEqual(reprint_receipt['store_email'], "support@globalsupermart.com")
        self.assertEqual(reprint_receipt['receipt_footer'], "Thanks for shopping! Valid for exchange in 7 days.")

    def test_batch_creation_for_same_sku_different_expiry(self):
        owner, profile, shop = self.create_tenant('batch_tenant')
        client = Client()
        client.login(username='batch_tenant', password='tenant123')

        # Add first batch
        res1 = client.post(reverse('products'), {
            'action': 'add_product',
            'name': 'Vitamin C 1000mg',
            'sku': 'VITC-1000',
            'cost_price': '500.00',
            'selling_price': '800.00',
            'stock_quantity': '50',
            'min_stock_level': '5',
            'expiry_date': '2026-12-01',
            'shop_id': shop.id,
        })
        self.assertEqual(res1.status_code, 302)
        batch1_prod = Product.objects.get(sku='VITC-1000', expiry_date='2026-12-01', shop=shop)
        self.assertEqual(batch1_prod.batch_number, 'BN: 1')

        # Add second batch of same product & same SKU but different expiry date
        res2 = client.post(reverse('products'), {
            'action': 'add_product',
            'name': 'Vitamin C 1000mg',
            'sku': 'VITC-1000',
            'cost_price': '550.00',
            'selling_price': '850.00',
            'stock_quantity': '30',
            'min_stock_level': '5',
            'expiry_date': '2027-06-01',
            'shop_id': shop.id,
        })
        self.assertEqual(res2.status_code, 302)
        batch2_prod = Product.objects.get(sku='VITC-1000', expiry_date='2027-06-01', shop=shop)
        self.assertEqual(batch2_prod.batch_number, 'BN: 2')

        # Add third batch
        res3 = client.post(reverse('products'), {
            'action': 'add_product',
            'name': 'Vitamin C 1000mg',
            'sku': 'VITC-1000',
            'cost_price': '550.00',
            'selling_price': '850.00',
            'stock_quantity': '40',
            'min_stock_level': '5',
            'expiry_date': '2028-01-01',
            'shop_id': shop.id,
        })
        self.assertEqual(res3.status_code, 302)
        batch3_prod = Product.objects.get(sku='VITC-1000', expiry_date='2028-01-01', shop=shop)
        self.assertEqual(batch3_prod.batch_number, 'BN: 3')

        # Attempt to create duplicate batch on same SKU in same shop
        res_dup = client.post(reverse('products'), {
            'action': 'add_product',
            'name': 'Vitamin C 1000mg',
            'sku': 'VITC-1000',
            'batch_number': 'BN: 2',
            'cost_price': '550.00',
            'selling_price': '850.00',
            'stock_quantity': '10',
            'min_stock_level': '5',
            'expiry_date': '2027-06-01',
            'shop_id': shop.id,
        })
        # Should stay on page with error, count stays 1
        self.assertEqual(Product.objects.filter(sku='VITC-1000', batch_number='BN: 2', shop=shop).count(), 1)

    def test_terms_and_agreement_view_and_registration_validation(self):
        # 1. Terms page accessible
        terms_res = self.client.get(reverse('terms_and_agreement'))
        self.assertEqual(terms_res.status_code, 200)
        self.assertContains(terms_res, "Terms of Service")
        self.assertContains(terms_res, "Merchant Agreement")

        # 2. Register without agree_terms fails
        self.verify_signup_email('noterms@test.com', first_name='No', last_name='Terms')
        fail_reg = self.client.post(reverse('register'), {
            'stage': 'complete',
            'username': 'notermsuser',
            'password': 'N0terms!SecurePass',
            'confirm_password': 'N0terms!SecurePass',
            'business_name': 'No Terms Shop',
            'business_type': 'GENERAL',
            'tenant_type': 'RETAIL',
            'kyc_document_type': 'National ID',
            'kyc_document_number': 'NIN-12345678',
        })
        self.assertEqual(fail_reg.status_code, 200)
        self.assertFalse(User.objects.filter(username='notermsuser').exists())

        # 3. Register with agree_terms succeeds
        self.verify_signup_email('terms@test.com', first_name='Terms', last_name='User')
        success_reg = self.client.post(reverse('register'), {
            'stage': 'complete',
            'username': 'termsuser',
            'password': 'VioletQuartz#7Signal',
            'confirm_password': 'VioletQuartz#7Signal',
            'business_name': 'Agreed Terms Shop',
            'business_type': 'GENERAL',
            'tenant_type': 'RETAIL',
            'kyc_document_type': 'National ID',
            'kyc_document_number': 'NIN-87654321',
            'agree_terms': 'on',
        })
        self.assertEqual(success_reg.status_code, 302)
        self.assertTrue(User.objects.filter(username='termsuser').exists())

    def test_admin_broadcast_notifications_and_read_api(self):
        from rms.models import AdminNotification, NotificationReadStatus
        owner, profile, shop = self.create_tenant('notify_tenant')
        client = Client()
        client.login(username='notify_tenant', password='tenant123')

        # Create broadcast via superadmin
        admin_client = Client()
        admin_client.login(username='admin', password='admin123')
        create_res = admin_client.post(reverse('admin_panel'), {
            'action': 'send_notification',
            'title': 'Scheduled Maintenance Tonight',
            'message': 'We will perform upgrades at 2am.',
            'notification_type': 'URGENT',
            'target_tenant_id': 'ALL',
        })
        self.assertEqual(create_res.status_code, 302)
        notif = AdminNotification.objects.get(title='Scheduled Maintenance Tonight')
        self.assertEqual(notif.notification_type, 'URGENT')
        self.assertIsNone(notif.target_tenant)

        # Check tenant sees unread notification
        dash_res = client.get(reverse('dashboard'))
        self.assertEqual(dash_res.status_code, 200)
        self.assertEqual(dash_res.context['unread_notifications_count'], 1)
        self.assertTrue(dash_res.context['has_urgent_notification'])

        # Mark as read via API
        mark_res = client.post(reverse('mark_notification_read_api'),
                               json.dumps({'notification_id': notif.id}),
                               content_type='application/json')
        self.assertEqual(mark_res.status_code, 200)
        self.assertEqual(mark_res.json()['status'], 'success')
        self.assertTrue(NotificationReadStatus.objects.filter(notification=notif, user=owner).exists())

        # Unread count should now be 0
        dash_res2 = client.get(reverse('dashboard'))
        self.assertEqual(dash_res2.context['unread_notifications_count'], 0)

    def test_tenant_deletion_with_confirmation(self):
        owner, profile, shop = self.create_tenant('deletable_tenant')
        admin_client = Client()
        admin_client.login(username='admin', password='admin123')

        # 1. Attempt delete without exact username confirmation -> rejected
        fail_del = admin_client.post(reverse('admin_panel'), {
            'action': 'delete_tenant',
            'user_id': owner.id,
            'delete_confirmation': 'wrong_name',
        })
        self.assertEqual(fail_del.status_code, 302)
        self.assertTrue(User.objects.filter(username='deletable_tenant').exists())

        # 2. Attempt delete with empty confirmation -> rejected
        fail_del2 = admin_client.post(reverse('admin_panel'), {
            'action': 'delete_tenant',
            'user_id': owner.id,
            'delete_confirmation': '',
        })
        self.assertEqual(fail_del2.status_code, 302)
        self.assertTrue(User.objects.filter(username='deletable_tenant').exists())

        # 3. Confirm deletion with exact username -> succeeds
        success_del = admin_client.post(reverse('admin_panel'), {
            'action': 'delete_tenant',
            'user_id': owner.id,
            'delete_confirmation': 'deletable_tenant',
        })
        self.assertEqual(success_del.status_code, 302)
        self.assertFalse(User.objects.filter(username='deletable_tenant').exists())

    def test_free_plan_14_day_expiry_and_restrictions(self):
        from datetime import timedelta
        from django.utils import timezone
        owner, profile, shop = self.create_tenant('trial_tenant')
        profile.plan = 'FREE'
        profile.trial_start_date = timezone.localdate()
        profile.save()

        # 1. Day 0 (Active trial)
        self.assertFalse(profile.is_trial_expired)
        self.assertEqual(profile.trial_days_remaining, 14)

        # 2. Day 10 (Still active)
        profile.trial_start_date = timezone.localdate() - timedelta(days=10)
        profile.save()
        self.assertFalse(profile.is_trial_expired)
        self.assertEqual(profile.trial_days_remaining, 4)

        # 3. Day 14 (Expired)
        profile.trial_start_date = timezone.localdate() - timedelta(days=14)
        profile.save()
        self.assertTrue(profile.is_trial_expired)
        self.assertEqual(profile.trial_days_remaining, 0)

        # 4. Attempt POS checkout with expired trial -> blocked (403)
        client = Client()
        client.login(username='trial_tenant', password='tenant123')
        product = Product.objects.create(
            name='Test Item',
            sku='TRIAL-SKU-1',
            category=self.category,
            selling_price=Decimal('100.00'),
            cost_price=Decimal('70.00'),
            stock_quantity=10,
            shop=shop
        )
        checkout_res = client.post(reverse('checkout_api'), json.dumps({
            'items': [{'product_id': product.id, 'quantity': 1, 'unit_price': '100.00'}],
            'payment_method': 'CASH'
        }), content_type='application/json')
        self.assertEqual(checkout_res.status_code, 403)
        self.assertIn('expired', checkout_res.json().get('error', '').lower())

        # 5. Upgrade to PREMIUM -> trial restrictions lifted immediately
        profile.plan = 'PREMIUM'
        profile.save()
        self.assertFalse(profile.is_trial_expired)
        checkout_res2 = client.post(reverse('checkout_api'), json.dumps({
            'items': [{'product_id': product.id, 'quantity': 1, 'unit_price': '100.00'}],
            'payment_method': 'CASH'
        }), content_type='application/json')
        self.assertEqual(checkout_res2.status_code, 200)

    def test_superuser_exemptions(self):
        from datetime import timedelta
        from django.utils import timezone
        from django.core.exceptions import PermissionDenied
        from rms.views import delete_tenant_accounts

        admin_user = User.objects.get(username='admin')
        self.assertTrue(admin_user.is_superuser)

        # 1. Superuser trial properties: always exempt even if trial_start_date is past
        admin_user.profile.trial_start_date = timezone.localdate() - timedelta(days=60)
        admin_user.profile.plan = 'FREE'
        admin_user.profile.save()
        self.assertFalse(admin_user.profile.is_trial_expired)
        self.assertIsNone(admin_user.profile.trial_days_remaining)

        # 2. Superuser cannot be deleted
        with self.assertRaises(PermissionDenied):
            delete_tenant_accounts(admin_user)
        self.assertTrue(User.objects.filter(username='admin').exists())

        # 3. Superuser is not in tenant lists in admin panel
        admin_client = Client()
        admin_client.login(username='admin', password='admin123')
        res = admin_client.get(reverse('admin_panel'))
        self.assertEqual(res.status_code, 200)

        # Verify admin is not in any tenant context
        all_tenants_users = [p.user.username for p in res.context['all_tenants']]
        pending_users = [p.user.username for p in res.context['pending_users']]
        tenant_options = [p.user.username for p in res.context['tenant_options']]
        self.assertNotIn('admin', all_tenants_users)
        self.assertNotIn('admin', pending_users)
        self.assertNotIn('admin', tenant_options)

        # Verify superuser does not see trial or approval banners
        self.assertNotContains(res, "Store Free Trial Expired")
        self.assertNotContains(res, "14-Day Free Trial Expired")
        self.assertNotContains(res, "Account Pending Approval")

        # 4. Attempt to pass superuser id to delete_tenant returns 404
        post_del = admin_client.post(reverse('admin_panel'), {
            'action': 'delete_tenant',
            'user_id': admin_user.id,
            'delete_confirmation': 'admin',
        })
        self.assertEqual(post_del.status_code, 404)
        self.assertTrue(User.objects.filter(username='admin').exists())

    def test_subscription_bank_transfer_and_admin_confirmation(self):
        from rms.models import SubscriptionPayment, AdminNotification
        owner, profile, shop = self.create_tenant('sub_tenant')
        profile.plan = 'FREE'
        profile.save()

        client = Client()
        client.login(username='sub_tenant', password='tenant123')

        # 1. Tenant accesses subscription pages
        res_sub = client.get(reverse('subscription'))
        self.assertEqual(res_sub.status_code, 200)
        res_upg = client.get(reverse('subscription_upgrade'))
        self.assertEqual(res_upg.status_code, 200)

        # 2. Tenant submits direct bank transfer for PREMIUM upgrade
        res_submit = client.post(reverse('subscription_upgrade'), {
            'plan': 'PREMIUM',
            'billing_cycle': 'MONTHLY',
            'payment_method': 'BANK_TRANSFER',
            'sender_name': 'Sub Tenant Business',
            'sender_bank': 'GTBank',
            'reference': 'TRANS-123456-ABC',
            'notes': 'Paid via mobile app transfer',
        })
        self.assertEqual(res_submit.status_code, 302)
        payment = SubscriptionPayment.objects.get(reference='TRANS-123456-ABC')
        self.assertEqual(payment.status, 'PENDING')
        self.assertEqual(payment.amount, Decimal('120000.00'))
        self.assertEqual(payment.tenant, owner)
        self.assertEqual(profile.plan, 'FREE')  # still free until confirmed

        # 3. Superadmin sees payment in admin panel and confirms upgrade
        admin_client = Client()
        admin_client.login(username='admin', password='admin123')
        admin_page = admin_client.get(reverse('admin_panel'))
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(payment, admin_page.context['pending_payments'])

        confirm_res = admin_client.post(reverse('admin_panel'), {
            'action': 'confirm_subscription',
            'payment_id': payment.id,
        })
        self.assertEqual(confirm_res.status_code, 302)

        payment.refresh_from_db()
        profile.refresh_from_db()
        self.assertEqual(payment.status, 'CONFIRMED')
        self.assertIsNotNone(payment.confirmed_at)
        self.assertEqual(profile.plan, 'PREMIUM')
        self.assertIsNone(profile.trial_start_date)
        self.assertIsNotNone(profile.subscription_expires_at)
        self.assertFalse(profile.is_trial_expired)

        # Notification sent to tenant
        self.assertTrue(AdminNotification.objects.filter(target_tenant=owner, title__icontains='Upgraded').exists())

    def test_subscription_remita_instant_verify_api(self):
        from rms.models import SubscriptionPayment
        owner, profile, shop = self.create_tenant('card_tenant')
        client = Client()
        client.login(username='card_tenant', password='tenant123')

        verify_res = client.post(reverse('remita_verify_api'), json.dumps({
            'rrr': 'RRR-TEST-999',
            'orderId': 'ORDER-999',
            'plan': 'GOLDEN_PREMIUM',
            'billing_cycle': 'ANNUAL'
        }), content_type='application/json')
        self.assertEqual(verify_res.status_code, 200)
        self.assertEqual(verify_res.json()['status'], 'success')

        payment = SubscriptionPayment.objects.get(reference='RRR-TEST-999')
        self.assertEqual(payment.status, 'CONFIRMED')
        self.assertEqual(payment.payment_method, 'REMITA')
        self.assertEqual(payment.amount, Decimal('250000.00'))

        profile.refresh_from_db()
        self.assertEqual(profile.plan, 'GOLDEN_PREMIUM')

    def test_company_bank_settings_update(self):
        from rms.models import StoreSettings
        admin_client = Client()
        admin_client.login(username='admin', password='admin123')

        update_res = admin_client.post(reverse('admin_panel'), {
            'action': 'update_bank_settings',
            'company_bank_name': 'Access Bank Plc',
            'company_account_name': 'viARMS Retail Tech',
            'company_account_number': '0987654321',
            'remita_merchant_id': '2547916',
            'remita_service_type_id': '4430731',
            'remita_api_key': '1946',
        })
        self.assertEqual(update_res.status_code, 302)
        settings = StoreSettings.get_settings()
        self.assertEqual(settings.company_bank_name, 'Access Bank Plc')
        self.assertEqual(settings.company_account_number, '0987654321')
        self.assertEqual(settings.remita_merchant_id, '2547916')
        self.assertEqual(settings.remita_service_type_id, '4430731')
        self.assertEqual(settings.remita_api_key, '1946')

    def test_remita_webhook_security_and_upgrade(self):
        import hashlib
        from rms.models import StoreSettings, SubscriptionPayment

        # Set known test secret key
        settings = StoreSettings.get_settings()
        test_key = 'remita_test_secret_api_key_12345'
        settings.remita_api_key = test_key
        settings.save()

        owner, profile, shop = self.create_tenant('webhook_tenant')
        profile.plan = 'FREE'
        profile.save()

        client = Client()

        notification_id = 'NOTIF-12345'
        amount = '15000.00'
        status = 'SUCCESSFUL'
        rrr = 'RRR-WEBHOOK-REF-777'
        order_id = f'VIARMS-{owner.id}-PRE-M-WEBHOOK77'

        payload = {
            'rrr': rrr,
            'orderId': order_id,
            'amount': amount,
            'paymentChannelName': status,
            'payerEmail': 'webhook@tenant.com',
            'notificationId': notification_id,
        }
        raw_body = json.dumps(payload).encode('utf-8')

        # 1. Invalid signature attempt -> Rejected (HTTP 400)
        res_invalid = client.post(
            reverse('remita_webhook'),
            data=raw_body,
            content_type='application/json',
            HTTP_X_REMITA_SIGNATURE='invalid_bogus_signature'
        )
        self.assertEqual(res_invalid.status_code, 400)
        profile.refresh_from_db()
        self.assertEqual(profile.plan, 'FREE')

        # 2. Valid SHA512 signature -> Processed & Upgraded (HTTP 200)
        # raw: apiKey + notificationId + amount + status + rrr
        raw_sig_str = f"{test_key}{notification_id}{amount}{status}{rrr}"
        valid_signature = hashlib.sha512(raw_sig_str.encode('utf-8')).hexdigest()

        res_valid = client.post(
            reverse('remita_webhook'),
            data=raw_body,
            content_type='application/json',
            HTTP_X_REMITA_SIGNATURE=valid_signature
        )
        self.assertEqual(res_valid.status_code, 200)

        profile.refresh_from_db()
        self.assertEqual(profile.plan, 'PREMIUM')
        self.assertIsNotNone(profile.subscription_expires_at)
        payment = SubscriptionPayment.objects.get(reference=rrr)
        self.assertEqual(payment.status, 'CONFIRMED')
        self.assertEqual(payment.payment_method, 'REMITA')

    def test_trial_contains_premium_features_and_standard_restricts_images(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        import io

        # 1. 14-day Free Trial contains all Premium features (including images)
        owner, profile, shop = self.create_tenant('trial_tenant')
        profile.plan = 'FREE'
        profile.trial_start_date = timezone.localdate()
        profile.save()
        self.assertTrue(profile.allows_product_images)

        # 2. Standard Plan does NOT allow product images
        owner_std, profile_std, shop_std = self.create_tenant('std_tenant')
        profile_std.plan = 'STANDARD'
        profile_std.save()
        self.assertFalse(profile_std.allows_product_images)

        # 3. Premium & Golden Premium allow product images
        profile_std.plan = 'PREMIUM'
        self.assertTrue(profile_std.allows_product_images)
        profile_std.plan = 'GOLDEN_PREMIUM'
        self.assertTrue(profile_std.allows_product_images)

        # 4. Standard Plan strips product image upon submission
        profile_std.plan = 'STANDARD'
        profile_std.save()

        client = Client()
        client.login(username='std_tenant', password='tenant123')

        img_file = io.BytesIO()
        Image.new('RGB', (100, 100), color='blue').save(img_file, format='JPEG')
        uploaded = SimpleUploadedFile('test.jpg', img_file.getvalue(), content_type='image/jpeg')

        res = client.post(reverse('products'), {
            'action': 'add_product',
            'name': 'Standard Panadol',
            'sku': 'SKU-STD-001',
            'cost_price': '100.00',
            'selling_price': '150.00',
            'stock_quantity': '50',
            'shop_id': shop_std.id,
            'image': uploaded,
        })
        self.assertEqual(res.status_code, 302)
        prod = Product.objects.get(sku='SKU-STD-001')
        self.assertFalse(bool(prod.image), "Product image must be stripped on the Standard Plan")

    def test_14_day_trial_auto_suspension_queues_persistent_email(self):
        from django.core import mail
        from rms.models import NotificationEmail
        from rms.views import check_and_process_tenant_lifecycle
        mail.outbox = []

        owner, profile, shop = self.create_tenant('expire_tenant')
        owner.email = 'expire@tenant.com'
        owner.save()
        profile.plan = 'FREE'
        # 15 days ago -> trial expired
        profile.trial_start_date = timezone.localdate() - timedelta(days=15)
        profile.save()

        self.assertTrue(profile.is_trial_expired)
        self.assertFalse(profile.is_suspended)

        # Run lifecycle check
        check_and_process_tenant_lifecycle(owner)

        profile.refresh_from_db()
        self.assertTrue(profile.is_suspended)
        self.assertTrue(profile.is_account_suspended)

        # In-app notification created
        self.assertTrue(AdminNotification.objects.filter(target_tenant=owner, title__icontains='Suspended').exists())

        # Email notice is durably queued for the worker, not sent inline.
        queued_email = NotificationEmail.objects.get(
            notification__target_tenant=owner,
            recipient='expire@tenant.com',
        )
        self.assertEqual(queued_email.status, 'PENDING')
        self.assertIn('Suspended', queued_email.subject)
        self.assertIn('not automatically deleted', queued_email.message)
        self.assertFalse(mail.outbox)

        # POS checkout is blocked
        client = Client()
        client.login(username='expire_tenant', password='tenant123')
        checkout_res = client.post(reverse('checkout_api'), json.dumps({
            'items': [{'id': 1, 'quantity': 1, 'price': 100}],
            'discount': 0,
            'payment_method': 'CASH',
        }), content_type='application/json')
        self.assertEqual(checkout_res.status_code, 403)
        self.assertIn('suspended', checkout_res.json()['error'].lower())

    def test_suspended_tenant_and_business_data_are_retained_after_30_days(self):
        from rms.views import check_and_process_tenant_lifecycle

        owner, profile, shop = self.create_tenant('tobe_deleted')
        product = Product.objects.create(
            name='Retained product',
            sku='RETAINED-001',
            cost_price=Decimal('2.00'),
            selling_price=Decimal('3.00'),
            stock_quantity=7,
            shop=shop,
        )
        profile.plan = 'FREE'
        profile.trial_start_date = timezone.localdate() - timedelta(days=50)
        profile.is_suspended = True
        profile.suspended_at = timezone.now() - timedelta(days=31)
        profile.save()

        self.assertGreaterEqual(profile.days_suspended, 30)

        # Expired accounts stay suspended; lifecycle processing must preserve their records.
        deleted = check_and_process_tenant_lifecycle(owner)
        self.assertFalse(deleted)
        self.assertTrue(User.objects.filter(username='tobe_deleted').exists())
        self.assertTrue(Product.objects.filter(id=product.id).exists())

    def test_standard_plan_remita_upgrade_and_unsuspension(self):
        from rms.models import SubscriptionPayment

        owner, profile, shop = self.create_tenant('redeem_tenant')
        profile.plan = 'FREE'
        profile.trial_start_date = timezone.localdate() - timedelta(days=16)
        profile.is_suspended = True
        profile.suspended_at = timezone.now() - timedelta(days=2)
        profile.save()
        self.assertTrue(profile.is_account_suspended)

        client = Client()
        client.login(username='redeem_tenant', password='tenant123')

        # Upgrade to STANDARD plan via Remita
        verify_res = client.post(reverse('remita_verify_api'), json.dumps({
            'rrr': 'RRR-TEST-STD-123',
            'orderId': 'ORDER-STD-123',
            'plan': 'STANDARD',
            'billing_cycle': 'MONTHLY'
        }), content_type='application/json')
        self.assertEqual(verify_res.status_code, 200)
        self.assertEqual(verify_res.json()['status'], 'success')

        payment = SubscriptionPayment.objects.get(reference='RRR-TEST-STD-123')
        self.assertEqual(payment.status, 'CONFIRMED')
        self.assertEqual(payment.amount, Decimal('80000.00'))
        self.assertEqual(payment.plan, 'STANDARD')

        profile.refresh_from_db()
        self.assertEqual(profile.plan, 'STANDARD')
        self.assertFalse(profile.is_suspended)
        self.assertFalse(profile.is_account_suspended)
        self.assertFalse(profile.allows_product_images)

    def test_staff_page_loads_for_admin_and_manager_and_manager_adds_scoped_cashier(self):
        owner, owner_profile, shop = self.create_tenant('staff_view_owner')
        manager = User.objects.create_user('staff_view_manager', password='manager123')
        manager_profile = manager.profile
        manager_profile.role = 'MANAGER'
        manager_profile.tenant_owner = owner
        manager_profile.shop = shop
        manager_profile.is_approved = True
        manager_profile.save()
        other_shop = Shop.objects.create(name='Other Staff Shop', tenant_owner=owner)

        admin_client = Client()
        self.assertTrue(admin_client.login(username='staff_view_owner', password='tenant123'))
        self.assertEqual(admin_client.get(reverse('staff_management')).status_code, 200)

        manager_client = Client()
        self.assertTrue(manager_client.login(username='staff_view_manager', password='manager123'))
        self.assertEqual(manager_client.get(reverse('staff_management')).status_code, 200)

        response = manager_client.post(reverse('staff_management'), {
            'action': 'add_staff',
            'username': 'staff_view_cashier',
            'email': 'cashier@gmail.com',
            'password': 'cashier123',
            'role': 'MANAGER',
            'shop_id': other_shop.id,
        })
        self.assertEqual(response.status_code, 302)

        cashier = User.objects.get(username='staff_view_cashier')
        self.assertEqual(cashier.profile.role, 'CASHIER')
        self.assertEqual(cashier.profile.shop, shop)
        self.assertEqual(cashier.profile.tenant_owner, owner)
