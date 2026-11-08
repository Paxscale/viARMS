from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from PIL import Image
from rms.models import Category, Product, Sale, SaleItem, Shop, StoreSettings
import json

class RMSModelsAndAPITests(TestCase):

    def setUp(self):
        # Create users
        self.admin_user = User.objects.create_superuser('admin', 'admin@test.com', 'admin123')
        self.cashier_user = User.objects.create_user('cashier', 'cashier@test.com', 'cashier123')

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
            cost_price=Decimal('0.50'),
            selling_price=Decimal('1.20'),
            stock_quantity=20,
            min_stock_level=5
        )

        self.product_chips = Product.objects.create(
            name="Chips 100g",
            sku="22222",
            category=self.category,
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
        registration_response = self.client.get(reverse('register'))
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
            self.assertContains(response, 'js/pos.js?v=3')
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
        registration_response = self.client.get(reverse('register'))
        self.assertContains(registration_response, 'value="BOOK_ACCESSORIES"')
        self.assertContains(registration_response, 'class="login-container register-page"')
        self.assertContains(registration_response, 'class="register-layout"')
        self.assertContains(registration_response, 'class="login-card glass-panel register-card"')
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
        self.client.login(username='admin', password='admin123')
        response = self.client.get(reverse('settings'))
        self.assertEqual(response.status_code, 200)
        
        # 3. Cashier can see dashboard
        self.client.login(username='cashier', password='cashier123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        # Verify cashier dashboard doesn't contain net profit string (it's hidden in the template)
        self.assertNotContains(response, "Today's Net Profit")

        # 4. Admin dashboard contains net profit
        self.client.login(username='admin', password='admin123')
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Today's Net Profit")
