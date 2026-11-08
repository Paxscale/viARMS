from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver

class Shop(models.Model):
    name = models.CharField(max_length=150)
    location = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    manager = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_shops')
    tenant_owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shops', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class UserProfile(models.Model):
    ROLES = [
        ('ADMIN', 'Admin'),
        ('MANAGER', 'Manager'),
        ('CASHIER', 'Cashier'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLES, default='CASHIER')
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff')

    # SaaS Fields
    tenant_owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tenant_members', null=True, blank=True)
    plan = models.CharField(max_length=20, choices=[('FREE', 'Free'), ('PREMIUM', 'Premium'), ('GOLDEN_PREMIUM', 'Golden Premium')], default='FREE')
    is_approved = models.BooleanField(default=False)
    KYC_STATUSES = [
        ('PENDING', 'Pending review'),
        ('VERIFIED', 'Verified'),
        ('RETURNED', 'More information needed'),
    ]
    kyc_status = models.CharField(max_length=20, choices=KYC_STATUSES, default='PENDING')
    kyc_document_type = models.CharField(max_length=60, blank=True)
    kyc_document_number = models.CharField(max_length=120, blank=True)
    kyc_notes = models.TextField(blank=True)
    kyc_reviewed_at = models.DateTimeField(null=True, blank=True)
    onboarding_completed = models.BooleanField(default=False)
    business_type = models.CharField(max_length=30, choices=[
        ('GENERAL', 'General Retail / Provision Store'),
        ('PHARMACEUTICAL', 'Pharmaceuticals'),
        ('BOOK_ACCESSORIES', 'Bookshop & Accessories'),
        ('UTENSILS', 'Utensils & Housewares')
    ], default='GENERAL')

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        role = 'ADMIN' if instance.is_superuser else 'CASHIER'
        UserProfile.objects.get_or_create(user=instance, defaults={'role': role})

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        instance.profile.save()
    else:
        role = 'ADMIN' if instance.is_superuser else 'CASHIER'
        UserProfile.objects.get_or_create(user=instance, defaults={'role': role})

class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    tenant_owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='categories', null=True, blank=True)

    class Meta:
        verbose_name_plural = "Categories"
        unique_together = [('name', 'tenant_owner')]

    def __str__(self):
        return self.name

class Product(models.Model):
    MATERIAL_CHOICES = [
        ('PLASTIC', 'Plastic'),
        ('CERAMIC', 'Ceramic'),
        ('FIBER', 'Fiber'),
        ('FABRIC', 'Fabric'),
        ('OTHER', 'Other'),
    ]

    name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50, db_index=True, verbose_name="SKU/Barcode")
    description = models.TextField(blank=True, null=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='products')
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Purchase/cost price of the product")
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Selling price of the product")
    stock_quantity = models.IntegerField(default=0, help_text="Current available stock")
    min_stock_level = models.IntegerField(default=5, help_text="Alert threshold for low stock")
    is_active = models.BooleanField(default=True)
    expiry_date = models.DateField(null=True, blank=True, help_text="Product expiration date")
    image = models.ImageField(upload_to='products/', blank=True, null=True)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='products', null=True, blank=True)

    # Business-specific customizable attributes
    # Pharmaceuticals
    dosage_form = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. Tablet, Syrup, Injection")
    generic_name = models.CharField(max_length=200, blank=True, null=True, help_text="Active pharmaceutical ingredient")
    prescription_required = models.BooleanField(default=False, help_text="Is prescription required to sell this item?")
    manufacturer = models.CharField(max_length=200, blank=True, null=True, help_text="Manufacturer or Pharmaceutical Company")
    strength = models.CharField(max_length=100, blank=True, null=True, help_text="Dosage strength or grams (e.g., 500mg, 10mg/5ml)")
    batch_number = models.CharField(max_length=100, blank=True, null=True, help_text="Manufacturing batch number")
    nafdac_reg_no = models.CharField(max_length=100, blank=True, null=True, verbose_name="NAFDAC Reg No")
    storage_conditions = models.CharField(max_length=200, blank=True, null=True, help_text="e.g., Store below 25°C")

    # Bookshop
    author = models.CharField(max_length=200, blank=True, null=True)
    publisher = models.CharField(max_length=200, blank=True, null=True)
    isbn = models.CharField(max_length=50, blank=True, null=True, verbose_name="ISBN")

    # Accessories
    color = models.CharField(max_length=50, blank=True, null=True)
    size = models.CharField(max_length=50, blank=True, null=True)
    warranty_period = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. 6 Months, 1 Year")
    material = models.CharField(max_length=20, choices=MATERIAL_CHOICES, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Same barcode (SKU) is allowed in same shop as long as expiry dates differ.
        # This supports stocking the same product with multiple batch expiry dates.
        unique_together = [('sku', 'shop', 'expiry_date')]

    def __str__(self):
        shop_prefix = f"[{self.shop.name}] " if self.shop else ""
        return f"{shop_prefix}{self.name} ({self.sku})"

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.min_stock_level

    @property
    def profit_margin(self):
        return self.selling_price - self.cost_price

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        return self.expiry_date <= timezone.localdate()

    @property
    def is_expiring_soon(self):
        if not self.expiry_date:
            return False
        today = timezone.localdate()
        return today < self.expiry_date <= today + timezone.timedelta(days=30)

class Sale(models.Model):
    PAYMENT_METHODS = [
        ('CASH', 'Cash'),
        ('CARD', 'Card'),
        ('MOBILE_MONEY', 'Mobile Money'),
    ]

    receipt_number = models.CharField(max_length=100, unique=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Tax rate in percentage (e.g. 15.00 for 15%)")
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Discount amount applied to the entire sale")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default='CASH')
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='sales')
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='sales', null=True, blank=True)

    def __str__(self):
        return f"Sale {self.receipt_number} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

    @property
    def total_cost(self):
        return sum(item.unit_cost_price * item.quantity for item in self.items.all())

    @property
    def net_profit(self):
        return self.total_amount - self.tax_amount - self.total_cost

class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, related_name='sale_items')
    quantity = models.IntegerField()
    unit_cost_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Cost price at the time of sale")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Selling price at the time of sale")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        product_name = self.product.name if self.product else "Deleted Product"
        return f"{self.quantity} x {product_name} in {self.sale.receipt_number}"

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

class StoreSettings(models.Model):
    store_name = models.CharField(max_length=200, default="Our Shop")
    address = models.TextField(blank=True, null=True, default="123 Retail Ave, Shoptown")
    phone = models.CharField(max_length=50, blank=True, null=True, default="+1 555-0199")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Default store tax rate in percentage")
    receipt_footer = models.TextField(blank=True, null=True, default="Thank you for shopping with us! Please come again.")

    def __str__(self):
        return self.store_name

    def save(self, *args, **kwargs):
        if not self.pk and StoreSettings.objects.exists():
            raise ValidationError("Only one instance of StoreSettings can exist.")
        return super(StoreSettings, self).save(*args, **kwargs)

    @classmethod
    def get_settings(cls):
        settings, created = cls.objects.get_or_create(pk=1)
        return settings


class Advertisement(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField(blank=True, null=True, help_text="Ad copy / message text")
    image_url = models.URLField(blank=True, null=True, help_text="URL to an image for this ad")
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='advertisements', null=True, blank=True,
                             help_text="Leave blank for a platform-wide advertisement")
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0, help_text="Lower numbers appear first")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', '-created_at']

    def __str__(self):
        return self.title
