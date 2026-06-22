from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from django.urls import reverse
from decimal import Decimal
from rms.models import Category, Product, Sale, SaleItem, StoreSettings
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

    def test_model_properties(self):
        # Test low stock alert
        self.product_chips.stock_quantity = 2
        self.assertTrue(self.product_chips.is_low_stock)

        self.product_chips.stock_quantity = 5
        self.assertFalse(self.product_chips.is_low_stock)

        # Test profit margin property
        self.assertEqual(self.product_chips.profit_margin, Decimal('1.20'))

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
