from decimal import Decimal
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

    TENANT_TYPE_CHOICES = [
        ('RETAIL', 'Retail'),
        ('WHOLESALE', 'Wholesale'),
        ('RETAIL_WHOLESALE', 'Retail and Wholesale'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLES, default='CASHIER')
    shop = models.ForeignKey(Shop, on_delete=models.SET_NULL, null=True, blank=True, related_name='staff')

    # SaaS Fields
    PLAN_CHOICES = [
        ('FREE', '14-Day Free Trial'),
        ('STANDARD', 'Standard'),
        ('PREMIUM', 'Premium'),
        ('GOLDEN_PREMIUM', 'Golden Premium'),
    ]
    tenant_owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tenant_members', null=True, blank=True)
    tenant_type = models.CharField(max_length=20, choices=TENANT_TYPE_CHOICES, default='RETAIL')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default='FREE')
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
        ('UTENSILS', 'Utensils & Housewares'),
        ('BOUTIQUE', 'Boutique')
    ], default='GENERAL')
    # Trial & Subscription tracking
    trial_start_date = models.DateField(null=True, blank=True, help_text="Date the free trial began")
    subscription_expires_at = models.DateField(null=True, blank=True, help_text="Date the paid subscription expires")
    is_suspended = models.BooleanField(default=False, help_text="True if tenant store account is suspended")
    suspended_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when account was suspended")
    suspension_reason = models.CharField(max_length=255, blank=True, default="", help_text="Reason for suspension")
    suspension_notified = models.BooleanField(default=False, help_text="True if suspension notice has been sent")

    @property
    def is_subscription_expired(self):
        """Returns True if a paid plan subscription has passed its expires_at date."""
        if self.user.is_superuser:
            return False
        target = self.tenant_owner.profile if (self.tenant_owner and self.tenant_owner != self.user and hasattr(self.tenant_owner, 'profile')) else self
        if target.user.is_superuser:
            return False
        if target.plan == 'FREE':
            return False
        if target.subscription_expires_at:
            from django.utils import timezone
            return timezone.localdate() > target.subscription_expires_at
        return False

    @property
    def has_active_subscription(self):
        """Returns True if the tenant has an active trial or an unexpired paid subscription."""
        if self.user.is_superuser:
            return True
        target = self.tenant_owner.profile if (self.tenant_owner and self.tenant_owner != self.user and hasattr(self.tenant_owner, 'profile')) else self
        if target.plan == 'FREE':
            return not target.is_trial_expired
        return not target.is_subscription_expired

    @property
    def is_trial_expired(self):
        """Returns True if this is a FREE plan account and the 14-day trial has ended.
        Superusers are always exempt."""
        if self.user.is_superuser:
            return False
        if self.tenant_owner and self.tenant_owner.is_superuser:
            return False
        target = self.tenant_owner.profile if (self.tenant_owner and self.tenant_owner != self.user and hasattr(self.tenant_owner, 'profile')) else self
        if target.user.is_superuser:
            return False
        if target.plan != 'FREE' or not target.trial_start_date:
            return False
        from django.utils import timezone
        return (timezone.localdate() - target.trial_start_date).days >= 14

    @property
    def trial_days_remaining(self):
        """Returns the number of days remaining in the free trial (0 if expired or not applicable).
        Superusers are always exempt."""
        if self.user.is_superuser:
            return None
        if self.tenant_owner and self.tenant_owner.is_superuser:
            return None
        target = self.tenant_owner.profile if (self.tenant_owner and self.tenant_owner != self.user and hasattr(self.tenant_owner, 'profile')) else self
        if target.user.is_superuser:
            return None
        if target.plan != 'FREE' or not target.trial_start_date:
            return None
        from django.utils import timezone
        days_used = (timezone.localdate() - target.trial_start_date).days
        remaining = 14 - days_used
        return max(0, remaining)

    @property
    def is_account_suspended(self):
        """Returns True if tenant account is currently suspended (expired trial or non-payment)."""
        if self.user.is_superuser:
            return False
        if self.tenant_owner and self.tenant_owner.is_superuser:
            return False
        target = self.tenant_owner.profile if (self.tenant_owner and self.tenant_owner != self.user and hasattr(self.tenant_owner, 'profile')) else self
        if target.user.is_superuser:
            return False
        if target.is_suspended:
            return True
        if target.plan == 'FREE' and target.is_trial_expired:
            return True
        if target.is_subscription_expired:
            return True
        return False

    @property
    def days_suspended(self):
        """Returns number of days elapsed since the account was suspended."""
        target = self.tenant_owner.profile if (self.tenant_owner and self.tenant_owner != self.user and hasattr(self.tenant_owner, 'profile')) else self
        if not target.is_account_suspended:
            return 0
        from django.utils import timezone
        if target.suspended_at:
            delta = timezone.now() - target.suspended_at
            return max(0, delta.days)
        if target.trial_start_date:
            from datetime import timedelta
            expired_date = target.trial_start_date + timedelta(days=14)
            return max(0, (timezone.localdate() - expired_date).days)
        return 0

    @property
    def allows_product_images(self):
        """
        Standard Plan should not have product image.
        14-Day Free Trial contains all Premium features (product images allowed).
        Premium and Golden Premium allow product images.
        """
        if self.user.is_superuser:
            return True
        target = self.tenant_owner.profile if (self.tenant_owner and self.tenant_owner != self.user and hasattr(self.tenant_owner, 'profile')) else self
        if target.user.is_superuser:
            return True
        if target.plan == 'FREE':
            return True
        if target.plan in ('PREMIUM', 'GOLDEN_PREMIUM'):
            return True
        # STANDARD plan does NOT allow product images
        return False

    def __str__(self):
        return f"{self.user.username} - {self.get_role_display()}"

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        role = 'ADMIN' if instance.is_superuser else 'CASHIER'
        defaults = {'role': role}
        if instance.is_superuser:
            defaults['is_approved'] = True
            defaults['kyc_status'] = 'VERIFIED'
        UserProfile.objects.get_or_create(user=instance, defaults=defaults)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    if hasattr(instance, 'profile'):
        if instance.is_superuser:
            if not instance.profile.is_approved:
                instance.profile.is_approved = True
            if instance.profile.kyc_status != 'VERIFIED':
                instance.profile.kyc_status = 'VERIFIED'
        instance.profile.save()
    else:
        role = 'ADMIN' if instance.is_superuser else 'CASHIER'
        defaults = {'role': role}
        if instance.is_superuser:
            defaults['is_approved'] = True
            defaults['kyc_status'] = 'VERIFIED'
        UserProfile.objects.get_or_create(user=instance, defaults=defaults)

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
    batch_number = models.CharField(max_length=100, blank=True, null=True, default='BN: 1', help_text="Manufacturing batch number")
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
        # Same barcode (SKU) is allowed in same shop across different batches.
        # This supports stocking the same product with multiple batch expiry dates.
        unique_together = [('sku', 'shop', 'batch_number')]
        constraints = [
            models.CheckConstraint(
                check=models.Q(cost_price__lte=models.F('selling_price')),
                name='product_cost_lte_selling_price',
            ),
        ]

    def clean(self):
        super().clean()
        if self.cost_price is not None and self.selling_price is not None and self.cost_price > self.selling_price:
            raise ValidationError({'cost_price': 'Cost price cannot be greater than selling price.'})

    def save(self, *args, **kwargs):
        if not self.batch_number or not str(self.batch_number).strip():
            self.batch_number = 'BN: 1'
        super().save(*args, **kwargs)

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
    phone = models.CharField(max_length=50, blank=True, null=True, default="+234 810 026 2697")
    email = models.EmailField(max_length=100, blank=True, null=True, default="info@ourshop.com")
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00, help_text="Default store tax rate in percentage")
    receipt_footer = models.TextField(blank=True, null=True, default="Thank you for shopping with viARMS Record Management System. Powered by Scales Technologies.")
    # Company payment details for tenant subscription upgrades
    company_bank_name = models.CharField(max_length=100, default="Zenith Bank", blank=True)
    company_account_name = models.CharField(max_length=150, default="viARMS Technologies Limited", blank=True)
    company_account_number = models.CharField(max_length=50, default="1012345678", blank=True)
    remita_merchant_id = models.CharField(max_length=150, blank=True, default="", help_text="Remita Merchant ID from your Remita profile (Biller ID)")
    remita_service_type_id = models.CharField(max_length=150, blank=True, default="", help_text="Remita Service Type ID assigned to this product/service")
    remita_api_key = models.CharField(max_length=255, blank=True, default="", help_text="Remita API Key - kept secret, never sent to the browser")

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


class AdminNotification(models.Model):
    NOTIFICATION_TYPES = [
        ('INFO', 'System Update / General Info'),
        ('WARNING', 'Important Notice'),
        ('URGENT', 'Critical Alert / Action Required'),
        ('FEATURE', 'New Feature Release'),
        ('SUCCESS', 'Successful Action'),
    ]

    title = models.CharField(max_length=200)
    message = models.TextField(help_text="Notification message or announcement body")
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='INFO')
    target_tenant = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True,
                                      related_name='received_admin_notifications',
                                      help_text="Leave blank to broadcast to all business accounts (tenants)")
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sent_admin_notifications')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_notification_type_display()}] {self.title}"

    @property
    def badge_color(self):
        mapping = {
            'INFO': 'badge-info',
            'WARNING': 'badge-warning',
            'URGENT': 'badge-danger',
            'FEATURE': 'badge-success',
        }
        return mapping.get(self.notification_type, 'badge-info')


class NotificationReadStatus(models.Model):
    notification = models.ForeignKey(AdminNotification, on_delete=models.CASCADE, related_name='read_records')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notification_read_statuses')
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('notification', 'user')]

    def __str__(self):
        return f"{self.user.username} read {self.notification.id}"


class NotificationEmail(models.Model):
    """Durable email outbox entry for an in-app system notification."""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
    ]

    notification = models.ForeignKey(
        AdminNotification, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='email_deliveries',
    )
    recipient = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.recipient} ({self.status})"


class StockInReport(models.Model):
    """Durable queue item for emailing stock-in details and an inventory workbook."""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('SENT', 'Sent'),
        ('FAILED', 'Failed'),
    ]

    tenant_owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='stock_in_reports')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='stock_in_reports_created')
    recipient = models.EmailField(blank=True)
    include_inventory_snapshot = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Stock-in report #{self.pk} ({self.status})"


class StockMovement(models.Model):
    """Historical record of stock added, with values captured at that time."""
    report = models.ForeignKey(StockInReport, on_delete=models.CASCADE, related_name='movements')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='stock_movements')
    product_name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50)
    category_name = models.CharField(max_length=200, blank=True)
    shop_name = models.CharField(max_length=150, blank=True)
    batch_number = models.CharField(max_length=100, blank=True)
    quantity_before = models.PositiveIntegerField()
    quantity_added = models.PositiveIntegerField()
    quantity_after = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'product_name']

    def __str__(self):
        return f"{self.product_name}: +{self.quantity_added}"


class ActivityLog(models.Model):
    """Compact, tenant-scoped record of important business actions."""
    tenant_owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='business_activity_logs')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='business_activity_actions')
    action = models.CharField(max_length=60, db_index=True)
    details = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['tenant_owner', '-created_at'], name='rms_act_tenant_time_idx')]

    def __str__(self):
        return f"{self.action}: {self.details[:80]}"


class AccountAppeal(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending review'),
        ('REVIEWED', 'Reviewed'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='account_appeals')
    reason = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default='PENDING', db_index=True)
    admin_response = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', '-created_at']

    def __str__(self):
        return f"Account appeal from {self.user.username} ({self.status})"

class SubscriptionPayment(models.Model):
    PAYMENT_METHODS = [
        ('REMITA', 'Debit Card / Remita'),
        ('BANK_TRANSFER', 'Direct Bank Transfer'),
    ]
    STATUS_CHOICES = [
        ('PENDING', 'Pending Confirmation'),
        ('CONFIRMED', 'Confirmed & Active'),
        ('REJECTED', 'Rejected'),
    ]
    BILLING_CYCLES = [
        ('QUARTERLY', 'Quarterly (3 Months)'),
        ('ANNUAL', 'Annual (12 Months)'),
        ('MONTHLY', 'Monthly (Legacy)'),
    ]
    PAYMENT_PLAN_CHOICES = [
        ('STANDARD', 'Standard'),
        ('PREMIUM', 'Premium'),
        ('GOLDEN_PREMIUM', 'Golden Premium'),
    ]
    PLAN_PRICING = {
        'STANDARD': {
            'ANNUAL': Decimal('80000.00'),
        },
        'PREMIUM': {
            'QUARTERLY': Decimal('35000.00'),
            'ANNUAL': Decimal('120000.00'),
        },
        'GOLDEN_PREMIUM': {
            'QUARTERLY': Decimal('70000.00'),
            'ANNUAL': Decimal('250000.00'),
        },
    }

    tenant = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscription_payments')
    plan = models.CharField(max_length=20, choices=PAYMENT_PLAN_CHOICES)
    billing_cycle = models.CharField(max_length=20, choices=BILLING_CYCLES, default='MONTHLY')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=30, choices=PAYMENT_METHODS, default='BANK_TRANSFER')
    reference = models.CharField(max_length=100, unique=True)
    sender_name = models.CharField(max_length=150, blank=True)
    sender_bank = models.CharField(max_length=100, blank=True)
    proof_image = models.ImageField(upload_to='subscription_proofs/', blank=True, null=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='confirmed_subscriptions')
    rejection_reason = models.TextField(blank=True)
    starts_at = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tenant.username} - {self.plan} ({self.status})"
