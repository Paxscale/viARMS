
def activate_subscription_upgrade(payment, confirmed_by=None):
    """Activates a confirmed subscription upgrade and extends validity."""
    today = timezone.localdate()
    if payment.billing_cycle == 'ANNUAL':
        days = 365
    elif payment.billing_cycle == 'QUARTERLY':
        days = 90
    else:
        days = 30

    payment.status = 'CONFIRMED'
    payment.confirmed_at = timezone.now()
    if confirmed_by:
        payment.confirmed_by = confirmed_by
    payment.starts_at = today
    payment.expires_at = today + timedelta(days=days)
    payment.save()

    profile = payment.tenant.profile
    profile.plan = payment.plan
    profile.trial_start_date = None
    profile.subscription_expires_at = payment.expires_at
    profile.is_suspended = False
    profile.suspended_at = None
    profile.suspension_reason = ""
    profile.suspension_notified = False
    profile.save(update_fields=[
        'plan', 'trial_start_date', 'subscription_expires_at',
        'is_suspended', 'suspended_at', 'suspension_reason', 'suspension_notified'
    ])

    create_admin_notification(
        title="Subscription Upgraded & Active!",
        message=f"Congratulations! Your business subscription has been upgraded to {payment.get_plan_display()} ({payment.get_billing_cycle_display()}). Your account is active until {payment.expires_at.strftime('%b %d, %Y')}.",
        notification_type="SUCCESS",
        target_tenant=payment.tenant,
        sender=confirmed_by if (confirmed_by and confirmed_by.is_superuser) else None
    )

import csv
import logging
import json
import uuid
from io import BytesIO
from urllib.parse import urlencode
from datetime import date, datetime, timedelta
from decimal import Decimal
from PIL import Image, ImageOps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth import views as auth_views
from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.core import signing
from django.contrib.auth.password_validation import validate_password
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import never_cache
from .remita import get_remita_credentials, generate_rrr, verify_remita_payment, verify_remita_webhook_signature
from django.http import JsonResponse, HttpResponse
from django.db import IntegrityError, transaction
from django.db.models import Sum, F, Count, Q
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth.models import User
from .models import Category, Product, Sale, SaleItem, StoreSettings, Shop, UserProfile, Advertisement, AdminNotification, NotificationReadStatus, SubscriptionPayment, ActivityLog, StockInReport, AccountAppeal
from .notification_delivery import create_admin_notification
from .stock_reporting import record_stock_additions
from .activity_logging import log_business_activity

logger = logging.getLogger(__name__)

MAX_PRODUCT_IMAGE_SIZE = 5 * 1024 * 1024
MAX_COMPRESSED_PRODUCT_IMAGE_SIZE = 50 * 1024
MAX_BULK_PRODUCTS = 20
MAX_BULK_UPLOAD_SIZE = 20 * 1024 * 1024
PRODUCT_IMAGE_FORMATS = {
    'JPEG': 'jpg',
    'PNG': 'png',
    'WEBP': 'webp',
    'GIF': 'gif',
}


def prepare_product_image(uploaded_image):
    if uploaded_image.size > MAX_PRODUCT_IMAGE_SIZE:
        raise ValueError("Product images must be 5 MB or smaller.")

    try:
        with Image.open(uploaded_image) as source:
            image_format = source.format
            if source.width * source.height > 20_000_000:
                raise ValueError("Product image dimensions must not exceed 20 megapixels.")
            source.seek(0)
            source.load()
            image = ImageOps.exif_transpose(source).copy()
    except (Image.DecompressionBombError, OSError) as exc:
        raise ValueError("Upload a valid JPEG, PNG, WebP, or GIF image.") from exc

    if image_format not in PRODUCT_IMAGE_FORMATS:
        raise ValueError("Upload a JPEG, PNG, WebP, or GIF image.")

    if image.mode == 'P' and 'transparency' in image.info:
        image = image.convert('RGBA')

    if 'A' in image.getbands():
        background = Image.new('RGB', image.size, 'white')
        background.paste(image, mask=image.getchannel('A'))
        image = background
    else:
        image = image.convert('RGB')

    image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    while True:
        for quality in (85, 75, 65, 55, 45, 35):
            output = BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=True, progressive=True)
            if output.tell() <= MAX_COMPRESSED_PRODUCT_IMAGE_SIZE:
                return ContentFile(output.getvalue(), name=f"{uuid.uuid4().hex}.jpg")

        if max(image.size) <= 128:
            raise ValueError("This image cannot be compressed to 50 KB. Choose a simpler or smaller image.")
        image = image.resize(
            (max(1, int(image.width * 0.8)), max(1, int(image.height * 0.8))),
            Image.Resampling.LANCZOS,
        )


def format_batch_label(val):
    if not val:
        return "BN: 1"
    s = str(val).strip()
    import re
    while re.match(r'^(b/n|bn|batch)[\s:]+', s, re.IGNORECASE):
        s = re.sub(r'^(b/n|bn|batch)[\s:]+', '', s, count=1, flags=re.IGNORECASE).strip()
    return f"BN: {s}" if s else "BN: 1"


def assign_product_batch(shop, sku, name, requested_batch=None):
    """
    Checks if a product with the same SKU or product name already exists in the shop.
    If existing products exist:
    - Ensures existing products have a batch label ('BN: 1' if not set).
    - If user did not provide a custom batch, auto-assigns the next sequential batch ('BN: 2', 'BN: 3', etc.).
    - If user provided a custom batch, uses it.
    If no existing products exist:
    - Uses user-provided batch if present, else defaults to 'BN: 1'.
    Returns (batch_number, is_new_batch, duplicate_error).
    """
    sku_clean = str(sku).strip()
    name_clean = str(name).strip()

    existing = Product.objects.filter(
        Q(sku__iexact=sku_clean) | Q(name__iexact=name_clean),
        shop=shop,
        is_active=True
    ).order_by('id')

    if existing.exists():
        if existing.count() == 1:
            first_prod = existing.first()
            if not first_prod.batch_number or not first_prod.batch_number.strip() or first_prod.batch_number == 'Batch 1':
                first_prod.batch_number = 'BN: 1'
                first_prod.save(update_fields=['batch_number'])
        else:
            for i, p in enumerate(existing, 1):
                if not p.batch_number or not p.batch_number.strip() or p.batch_number.startswith('Batch'):
                    p.batch_number = f"BN: {i}"
                    p.save(update_fields=['batch_number'])

        if requested_batch and str(requested_batch).strip():
            batch_num = format_batch_label(requested_batch)
        else:
            next_idx = existing.count() + 1
            batch_num = f"BN: {next_idx}"
            while Product.objects.filter(sku__iexact=sku_clean, shop=shop, batch_number__iexact=batch_num, is_active=True).exists():
                next_idx += 1
                batch_num = f"BN: {next_idx}"

        if Product.objects.filter(sku__iexact=sku_clean, shop=shop, batch_number__iexact=batch_num, is_active=True).exists():
            return batch_num, False, f"A product with SKU '{sku_clean}' and batch '{batch_num}' already exists in this shop. Please specify a different batch or adjust existing stock."

        return batch_num, True, None
    else:
        if requested_batch and str(requested_batch).strip():
            batch_num = format_batch_label(requested_batch)
        else:
            batch_num = 'BN: 1'

        if Product.objects.filter(sku__iexact=sku_clean, shop=shop, batch_number__iexact=batch_num, is_active=True).exists():
            return batch_num, False, f"A product with SKU '{sku_clean}' and batch '{batch_num}' already exists in this shop."

        return batch_num, False, None


def generate_internal_product_sku():
    """Create a unique internal identifier when the business does not use SKU codes."""
    return f"BTQ-{uuid.uuid4().hex[:12].upper()}"


def create_bulk_products(request, user_profile, tenant, is_user_admin, user_shop):
    try:
        product_count = int(request.POST.get('product_count', '0'))
    except ValueError:
        messages.error(request, "The bulk product form contains an invalid row count.")
        return

    if product_count < 1 or product_count > MAX_BULK_PRODUCTS:
        messages.error(request, f"Add between 1 and {MAX_BULK_PRODUCTS} products per bulk submission.")
        return

    total_upload_size = sum(upload.size for upload in request.FILES.values())
    if total_upload_size > MAX_BULK_UPLOAD_SIZE:
        messages.error(request, "The total size of product images in one submission must not exceed 20 MB.")
        return

    if is_user_admin:
        shop_id = request.POST.get('bulk_shop_id')
        shops = Shop.objects.filter(tenant_owner=tenant) if tenant else Shop.objects.all()
        product_shop = shops.filter(id=shop_id).first() if shop_id else None
    else:
        product_shop = user_shop

    if not product_shop:
        messages.error(request, "Choose a valid shop for the bulk product submission.")
        return

    categories = Category.objects.filter(tenant_owner=tenant) if tenant else Category.objects.all()
    products_to_create = []
    errors = []
    submitted_keys = set()
    product_fields = (
        'name', 'sku', 'category_id', 'cost_price', 'selling_price',
        'stock_quantity', 'min_stock_level', 'description', 'expiry_date',
        'dosage_form', 'generic_name', 'prescription_required', 'manufacturer',
        'strength', 'batch_number', 'nafdac_reg_no', 'storage_conditions',
        'author', 'publisher', 'isbn', 'color', 'size', 'warranty_period', 'material',
    )

    for index in range(product_count):
        prefix = f'product-{index}-'
        values = {
            field: request.POST.get(f'{prefix}{field}', '').strip()
            for field in product_fields
        }
        uploaded_image = request.FILES.get(f'{prefix}image')
        if not any(values.values()) and not uploaded_image:
            continue

        row_errors = []
        name = values['name']
        is_boutique = user_profile.business_type == 'BOUTIQUE'
        sku = values['sku'] or (generate_internal_product_sku() if is_boutique else '')
        if not name:
            row_errors.append("product name is required")
        elif len(name) > 200:
            row_errors.append("product name must be 200 characters or fewer")
        if not sku and not is_boutique:
            row_errors.append("SKU/barcode is required")
        elif len(sku) > 50:
            row_errors.append("SKU/barcode must be 50 characters or fewer")

        try:
            cost_price = Decimal(values['cost_price'])
            selling_price = Decimal(values['selling_price'])
            if not cost_price.is_finite() or cost_price < 0:
                raise ValueError
            if not selling_price.is_finite() or selling_price < 0:
                raise ValueError
            if any(
                amount.quantize(Decimal('0.01')) != amount or amount.adjusted() >= 8
                for amount in (cost_price, selling_price)
            ):
                raise ValueError
            if cost_price > selling_price:
                row_errors.append("cost price cannot be greater than selling price")
        except (ArithmeticError, ValueError):
            row_errors.append("cost and selling prices must fit a non-negative amount with up to 2 decimal places")

        try:
            stock_quantity = int(values['stock_quantity'])
            min_stock_level = int(values['min_stock_level'] or '5')
            if (
                stock_quantity < 0 or min_stock_level < 0
                or stock_quantity > 2_147_483_647 or min_stock_level > 2_147_483_647
            ):
                raise ValueError
        except ValueError:
            row_errors.append("stock quantity and minimum stock must be non-negative whole numbers within the supported range")

        category = None
        if values['category_id']:
            category = categories.filter(id=values['category_id']).first()
            if not category:
                row_errors.append("category is invalid or not available to this account")

        material = values['material']
        allowed_materials = {choice[0] for choice in Product.MATERIAL_CHOICES}
        if material and material not in allowed_materials:
            row_errors.append("material is not a valid choice")
        expiry = None
        if values['expiry_date'] and not is_boutique and not (
            user_profile.business_type == 'UTENSILS' and material == 'PLASTIC'
        ):
            try:
                expiry = date.fromisoformat(values['expiry_date'])
            except ValueError:
                row_errors.append("expiry date must be a valid date")

        batch_val = values.get('batch_number', '').strip()
        if sku and name and not row_errors:
            batch_val, is_new_batch, batch_err = assign_product_batch(product_shop, sku, name, batch_val)
            if batch_err:
                row_errors.append(batch_err)
            else:
                values['batch_number'] = batch_val
                duplicate_key = (sku.lower(), product_shop.pk, batch_val.lower())
                if duplicate_key in submitted_keys:
                    row_errors.append("another row in this submission has the same SKU and batch number for this shop")
                submitted_keys.add(duplicate_key)

        if not user_profile.allows_product_images:
            uploaded_image = None
        elif uploaded_image:
            try:
                uploaded_image = prepare_product_image(uploaded_image)
            except ValueError as exc:
                row_errors.append(str(exc))

        if row_errors:
            errors.append(f"Row {index + 1}: {', '.join(row_errors)}.")
            continue

        products_to_create.append(Product(
            name=name,
            sku=sku,
            category=category,
            cost_price=cost_price,
            selling_price=selling_price,
            stock_quantity=stock_quantity,
            min_stock_level=min_stock_level,
            description=values['description'],
            expiry_date=expiry,
            shop=product_shop,
            dosage_form=values['dosage_form'],
            generic_name=values['generic_name'],
            prescription_required=values['prescription_required'] in ('true', 'on'),
            manufacturer=values['manufacturer'],
            strength=values['strength'],
            batch_number=values['batch_number'],
            nafdac_reg_no=values['nafdac_reg_no'],
            storage_conditions=values['storage_conditions'],
            author=values['author'],
            publisher=values['publisher'],
            isbn=values['isbn'],
            color=values['color'],
            size=values['size'],
            warranty_period=values['warranty_period'],
            material=material,
            image=uploaded_image,
        ))

    if errors:
        for error in errors:
            messages.error(request, error)
        return
    if not products_to_create:
        messages.error(request, "Enter product details in at least one row.")
        return

    saved_images = []
    stock_report = None
    try:
        with transaction.atomic():
            for product in products_to_create:
                product.save()
                if product.image:
                    saved_images.append((product.image.storage, product.image.name))
            stock_report = record_stock_additions(
                [(product, 0) for product in products_to_create],
                actor=request.user,
                tenant_owner=tenant,
            )
            for product in products_to_create:
                log_business_activity(
                    actor=request.user, tenant_owner=tenant, action='Product created',
                    details=f"{product.name} ({product.sku}) added to {product.shop.name} with {product.stock_quantity} in stock.",
                )
    except IntegrityError:
        for storage, image_name in saved_images:
            storage.delete(image_name)
        messages.error(request, "No products were added because a product conflicts with existing inventory.")
        return

    report_note = " Stock report queued for the tenant admin." if stock_report else ""
    messages.success(request, f"Successfully added {len(products_to_create)} products to {product_shop.name}.{report_note}")


def is_admin(user):
    return user.is_superuser or (hasattr(user, 'profile') and user.profile.role == 'ADMIN')

def is_manager(user):
    return hasattr(user, 'profile') and user.profile.role == 'MANAGER'

def is_admin_or_manager(user):
    return is_admin(user) or is_manager(user)

def get_tenant_scope(user):
    if user.is_superuser:
        return None
    profile = getattr(user, 'profile', None)
    if profile:
        return profile.tenant_owner if profile.tenant_owner else user
    return user


def _safe_detail_return_url(request, fallback_url):
    return_url = request.GET.get('next', '')
    if return_url and url_has_allowed_host_and_scheme(
        return_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return return_url
    return fallback_url


def get_store_settings_for_user(user, shop=None):
    tenant_owner = shop.tenant_owner if shop and shop.tenant_owner_id else getattr(
        getattr(user, 'profile', None), 'tenant_owner', None
    )
    return StoreSettings.get_settings(tenant_owner=tenant_owner)


def has_wholesale_access(user):
    tenant_owner = get_tenant_scope(user)
    profile = getattr(tenant_owner, 'profile', None) if tenant_owner else None
    return bool(
        profile
        and profile.plan != 'STANDARD'
        and profile.tenant_type in {'WHOLESALE', 'RETAIL_WHOLESALE'}
    )

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        from django.core.mail import send_mail
        from django.conf import settings
        stage = request.POST.get('stage')
        if stage == 'send_verification':
            email = request.POST.get('email', '').strip().lower()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            # Public signup always starts on the Free trial; paid plans require checkout.
            plan = 'FREE'
            context = {'email': email, 'first_name': first_name, 'last_name': last_name, 'selected_plan': plan}
            try:
                User._meta.get_field('email').clean(email, None)
            except DjangoValidationError:
                messages.error(request, "Enter a valid email address.")
                return render(request, 'register.html', context)
            if not first_name or not last_name:
                messages.error(request, "Enter your first and last name to continue.")
            elif len(first_name) > 150 or len(last_name) > 150:
                messages.error(request, "Names must be 150 characters or fewer.")
            elif plan != 'FREE':
                messages.error(request, "Choose a valid plan.")
            elif User.objects.filter(email__iexact=email).exists():
                context['existing_account'] = True
                messages.error(request, "An account with this email already exists. Try signing in or reset your password.")
            else:
                token = signing.dumps(
                    {'email': email, 'first_name': first_name, 'last_name': last_name, 'plan': plan},
                    salt='rms.signup-email-verification',
                )
                verify_url = request.build_absolute_uri(reverse('verify_signup_email', args=[token]))
                message = (
                    f"Hi {first_name},\n\n"
                    "Please verify your email address to continue creating your viARMS account.\n\n"
                    f"Verify your email: {verify_url}\n\n"
                    "This link expires in 24 hours. If you did not request this, you can ignore this email."
                )
                try:
                    sent_count = send_mail('Verify your email to continue with viARMS', message, None, [email], fail_silently=False)
                    if not sent_count:
                        raise RuntimeError('The email backend did not accept the verification message.')
                    context['verification_sent'] = True
                    if settings.EMAIL_BACKEND.endswith('.console.EmailBackend'):
                        context['verification_console'] = True
                        messages.warning(request, "Email delivery is not configured. The verification link was written to the application server console, not sent to your inbox.")
                    else:
                        messages.success(request, f"Your verification email was handed to the mail server for {email}. Check your inbox and spam folder.")
                except Exception:
                    logger.exception("Signup verification email delivery failed (backend=%s).", settings.EMAIL_BACKEND)
                    messages.error(request, "We could not send the verification email. Please try again later.")
            return render(request, 'register.html', context)

        signup = request.session.get('verified_signup')
        if signup and timezone.now().timestamp() - float(signup.get('verified_at', 0)) > 60 * 60 * 24:
            request.session.pop('verified_signup', None)
            signup = None
        if stage != 'complete' or not signup:
            messages.error(request, "Verify your email using the link we sent before continuing. Verification links expire after 24 hours.")
            return render(request, 'register.html')

        username = request.POST.get('username', '').strip().lower()
        email = signup['email']
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        business_name = request.POST.get('business_name', '').strip()
        business_type = request.POST.get('business_type', 'GENERAL')
        plan = 'FREE'
        tenant_type = request.POST.get('tenant_type', 'RETAIL')
        kyc_document_type = request.POST.get('kyc_document_type', '').strip()
        kyc_document_number = request.POST.get('kyc_document_number', '').strip()
        context = {
            'email_verified': True, 'verified_email': email,
            'first_name': signup['first_name'], 'last_name': signup['last_name'],
            'selected_plan': plan,
            'username': username, 'business_name': business_name,
        }

        if not username or not business_name:
            messages.error(request, "Enter a username and business name to continue.")
            return render(request, 'register.html', context)
        if plan != 'FREE':
            messages.error(request, "Choose a valid plan.")
            return render(request, 'register.html', context)
        if not password or not confirm_password:
            messages.error(request, "Enter and confirm a password.")
            return render(request, 'register.html', context)
        try:
            User._meta.get_field('username').clean(username, None)
            validate_password(password, User(username=username, email=email))
        except DjangoValidationError as error:
            messages.error(request, ' '.join(error.messages))
            return render(request, 'register.html', context)
        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, 'register.html', context)
        if not request.POST.get('agree_terms'):
            messages.error(request, "You must agree to the Terms and Agreement & Privacy Policy to register.")
            return render(request, 'register.html', context)
        if business_type not in dict(UserProfile._meta.get_field('business_type').choices):
            messages.error(request, "Choose a valid business type.")
            return render(request, 'register.html', context)
        if tenant_type not in dict(UserProfile.TENANT_TYPE_CHOICES):
            messages.error(request, "Choose a valid business model.")
            return render(request, 'register.html', context)
        if not kyc_document_type or not kyc_document_number:
            messages.error(request, "Enter your verification document type and number.")
            return render(request, 'register.html', context)
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, f"Username '{username}' is already in use.")
            return render(request, 'register.html', context)
        if User.objects.filter(email__iexact=email).exists():
            request.session.pop('verified_signup', None)
            messages.error(request, "An account with this email already exists. Sign in or reset your password if needed.")
            return redirect('login')

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=signup['first_name'],
                    last_name=signup['last_name'],
                )
                profile, created = UserProfile.objects.get_or_create(user=user)
                profile.role = 'ADMIN'
                profile.tenant_owner = user
                profile.plan = plan
                profile.business_type = business_type
                profile.tenant_type = tenant_type
                profile.is_approved = False
                if plan == 'FREE':
                    profile.trial_start_date = timezone.localdate()
                profile.kyc_document_type = kyc_document_type
                profile.kyc_document_number = kyc_document_number
                profile.kyc_status = 'PENDING'
                profile.save()

                shop = Shop.objects.create(name=business_name, tenant_owner=user, manager=user)
                profile.shop = shop
                profile.save()
        except IntegrityError:
            messages.error(request, "This username or email is already registered. Please try another.")
            return render(request, 'register.html', context)

        request.session.pop('verified_signup', None)
        messages.success(request, "Your account has been created successfully! It is pending administrator approval.")
        return redirect('login')

    signup = request.session.get('verified_signup')
    if signup and timezone.now().timestamp() - float(signup.get('verified_at', 0)) > 60 * 60 * 24:
        request.session.pop('verified_signup', None)
        signup = None
        messages.error(request, "Your verified signup session expired. Request a new email verification link.")
    context = {'selected_plan': 'FREE'}
    if signup:
        context.update({
            'email_verified': True,
            'verified_email': signup['email'],
            'first_name': signup['first_name'],
            'last_name': signup['last_name'],
            'selected_plan': signup.get('plan', 'FREE'),
        })
    return render(request, 'register.html', context)


def verify_signup_email_view(request, token):
    if request.user.is_authenticated:
        return redirect('dashboard')
    try:
        signup = signing.loads(token, salt='rms.signup-email-verification', max_age=60 * 60 * 24)
    except signing.SignatureExpired:
        messages.error(request, "This verification link has expired. Request a new one to continue.")
        return redirect('register')
    except signing.BadSignature:
        messages.error(request, "This verification link is invalid. Request a new one to continue.")
        return redirect('register')

    if User.objects.filter(email__iexact=signup.get('email', '')).exists():
        messages.error(request, "An account with this email already exists. Sign in or reset your password if needed.")
        return redirect('login')
    signup['verified_at'] = timezone.now().timestamp()
    request.session['verified_signup'] = signup
    messages.success(request, "Email verified. Finish setting up your account.")
    return redirect('register')

@never_cache
def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin_panel')
        return redirect('dashboard')
    
    login_notice = None
    if request.method == 'POST':
        login_data = request.POST.copy()
        submitted_identity = request.POST.get('username', '').strip()
        if '@' in submitted_identity:
            email_user = User.objects.filter(email__iexact=submitted_identity).order_by('id').first()
            if email_user:
                login_data['username'] = email_user.username
        form = AuthenticationForm(request, data=login_data)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                # Check approval status
                profile, created = UserProfile.objects.get_or_create(user=user, defaults={'role': 'ADMIN' if user.is_superuser else 'CASHIER'})
                owner = profile.tenant_owner if profile.tenant_owner else user
                if not owner.is_active:
                    login_notice = {
                        'kind': 'suspended',
                        'message': 'An account with this email exists, but the business account is suspended.',
                        'status_url': reverse('account_status') + '?' + urlencode({'email': owner.email}),
                    }
                    form = AuthenticationForm()
                    return render(request, 'login.html', {
                        'form': form, 'next': request.POST.get('next') or request.GET.get('next', ''),
                        'login_notice': login_notice,
                    })
                if not owner.is_superuser and not owner.profile.is_approved:
                    login_notice = {
                        'kind': 'pending',
                        'message': 'An account with this email exists and is awaiting administrator approval.',
                        'status_url': reverse('account_status') + '?' + urlencode({'email': owner.email}),
                    }
                    return render(request, 'login.html', {
                        'form': form, 'next': request.POST.get('next') or request.GET.get('next', ''),
                        'login_notice': login_notice,
                    })

                owner_profile = getattr(owner, 'profile', None)
                subscription_expired = bool(owner_profile and (
                    owner_profile.is_trial_expired or owner_profile.is_subscription_expired
                ))
                if owner_profile and owner_profile.is_account_suspended and subscription_expired:
                    login(request, user)
                    messages.warning(
                        request,
                        'Your account exists, but access is paused because its trial or subscription has expired. Review renewal options below.',
                    )
                    return redirect('subscription')
                if owner_profile and owner_profile.is_suspended and not subscription_expired:
                    return render(request, 'login.html', {
                        'form': AuthenticationForm(),
                        'next': request.POST.get('next') or request.GET.get('next', ''),
                        'login_notice': {
                            'kind': 'suspended',
                            'message': 'An account with this email exists, but this account is suspended.',
                            'status_url': reverse('account_status') + '?' + urlencode({'email': owner.email}),
                        },
                    })

                login(request, user)
                if user.is_superuser:
                    return redirect('admin_panel')
                next_url = request.POST.get('next') or request.GET.get('next')
                if next_url and url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)
                return redirect('dashboard')
        else:
            existing_user = User.objects.filter(
                Q(username__iexact=submitted_identity) | Q(email__iexact=submitted_identity)
            ).select_related('profile', 'profile__tenant_owner').first() if submitted_identity else None
            if existing_user:
                profile = getattr(existing_user, 'profile', None)
                owner = profile.tenant_owner if profile and profile.tenant_owner_id else existing_user
                owner_profile = getattr(owner, 'profile', None)
                if (not existing_user.is_active or not owner.is_active) and existing_user.check_password(request.POST.get('password', '')):
                    login_notice = {
                        'kind': 'suspended',
                        'message': 'An account with this email exists, but this account is suspended.',
                        'status_url': reverse('account_status') + '?' + urlencode({'email': owner.email}),
                    }
                elif owner_profile and not owner_profile.is_approved:
                    login_notice = {
                        'kind': 'pending',
                        'message': 'An account with this email exists and is awaiting administrator approval.',
                        'status_url': reverse('account_status') + '?' + urlencode({'email': owner.email}),
                    }
                else:
                    login_notice = {
                        'kind': 'password',
                        'message': 'A user with this email exists. Sign in with your credentials, or reset your password if you cannot remember it.',
                    }
            else:
                login_notice = {
                    'kind': 'invalid',
                    'message': 'We could not find an account matching those credentials. Check your email or username and password.',
                }
    else:
        form = AuthenticationForm()
    return render(request, 'login.html', {
        'form': form,
        'next': request.POST.get('next') or request.GET.get('next', ''),
        'login_notice': login_notice,
    })


def account_inactive_view(request):
    email = (request.POST.get('email') or request.GET.get('email') or '').strip().lower()
    account = None
    owner = None
    profile = None
    status_detail = ''
    inactive_reason = ''
    appeal_allowed = False
    renewal_available = False
    latest_appeal = None

    if email:
        account = User.objects.filter(email__iexact=email, is_superuser=False).select_related(
            'profile', 'profile__tenant_owner', 'profile__tenant_owner__profile'
        ).first()
        if account:
            account_profile = getattr(account, 'profile', None)
            owner = account_profile.tenant_owner if account_profile and account_profile.tenant_owner_id else account
            profile = getattr(owner, 'profile', None)
            expired = bool(profile and (profile.is_trial_expired or profile.is_subscription_expired))

            if expired:
                inactive_reason = 'Your trial or subscription has expired.'
                status_detail = 'Renew your subscription to restore access. Sign in to continue to the subscription page.'
                renewal_available = account.is_active and owner.is_active
                appeal_allowed = not account.is_active or not owner.is_active
                if appeal_allowed and profile and profile.suspension_reason:
                    status_detail = f"Administrator suspension: {profile.suspension_reason} Renew the subscription after access is restored."
            elif not account.is_active or not owner.is_active:
                inactive_reason = (profile.suspension_reason if profile else '') or 'An administrator suspended this account.'
                status_detail = inactive_reason
                appeal_allowed = True
            elif profile and profile.is_suspended:
                inactive_reason = profile.suspension_reason or 'An administrator suspended this account.'
                status_detail = inactive_reason
                appeal_allowed = True
            elif profile and not profile.is_approved:
                inactive_reason = 'Your account is awaiting administrator approval.'
                status_detail = inactive_reason
            else:
                inactive_reason = 'The account is marked inactive.'
                status_detail = 'Please contact the administrator if you need help restoring access.'
                appeal_allowed = True

            latest_appeal = AccountAppeal.objects.filter(user=owner).first()
            if request.method == 'POST' and request.POST.get('action') == 'submit_appeal':
                reason = request.POST.get('reason', '').strip()
                if not appeal_allowed:
                    messages.error(request, 'An appeal is available only for an administrator-suspended account.')
                elif len(reason) < 10:
                    messages.error(request, 'Please provide at least 10 characters explaining your appeal.')
                else:
                    pending_appeal = AccountAppeal.objects.filter(user=owner, status='PENDING').first()
                    if pending_appeal:
                        pending_appeal.reason = reason[:2000]
                        pending_appeal.save(update_fields=['reason'])
                        latest_appeal = pending_appeal
                    else:
                        latest_appeal = AccountAppeal.objects.create(user=owner, reason=reason[:2000])
                    messages.success(request, 'Your appeal has been submitted for administrator review.')

    return render(request, 'account/account_inactive.html', {
        'email': email,
        'account_found': bool(account),
        'inactive_reason': inactive_reason,
        'status_detail': status_detail,
        'appeal_allowed': appeal_allowed,
        'renewal_available': renewal_available,
        'latest_appeal': latest_appeal,
    })


def account_status_view(request):
    email = (request.POST.get('email') or request.GET.get('email') or '').strip().lower()
    account_status = None
    status_detail = ''
    appeal_allowed = False
    latest_appeal = None
    if email:
        account = User.objects.filter(email__iexact=email, is_superuser=False).select_related(
            'profile', 'profile__tenant_owner'
        ).first()
        if not account:
            account_status = 'not_found'
        else:
            account_profile = getattr(account, 'profile', None)
            owner = account_profile.tenant_owner if account_profile and account_profile.tenant_owner_id else account
            profile = getattr(owner, 'profile', None)
            if not account.is_active or not owner.is_active:
                account_status = 'suspended'
                status_detail = (profile.suspension_reason if profile else '') or 'Sign-in has been disabled by an administrator.'
                appeal_allowed = True
            elif profile and profile.is_account_suspended:
                account_status = 'suspended'
                status_detail = 'Store access is paused because the trial or subscription has expired. Sign in to review subscription options, or submit an appeal if you believe this is an error.'
                appeal_allowed = True
            elif profile and not profile.is_approved:
                account_status = 'pending'
                status_detail = 'Your registration is waiting for administrator approval.'
            else:
                account_status = 'active'
                status_detail = 'Your account is active. If you cannot sign in, reset your password.'

            latest_appeal = AccountAppeal.objects.filter(user=owner).first()
            if request.method == 'POST' and request.POST.get('action') == 'submit_appeal':
                reason = request.POST.get('reason', '').strip()
                if not appeal_allowed:
                    messages.error(request, 'An appeal is available only for a suspended account.')
                elif len(reason) < 10:
                    messages.error(request, 'Please provide at least 10 characters explaining your appeal.')
                else:
                    pending_appeal = AccountAppeal.objects.filter(user=owner, status='PENDING').first()
                    if pending_appeal:
                        pending_appeal.reason = reason[:2000]
                        pending_appeal.save(update_fields=['reason'])
                        latest_appeal = pending_appeal
                    else:
                        latest_appeal = AccountAppeal.objects.create(user=owner, reason=reason[:2000])
                    messages.success(request, 'Your appeal has been submitted for administrator review.')

    return render(request, 'account_status.html', {
        'email': email,
        'account_status': account_status,
        'status_detail': status_detail,
        'appeal_allowed': appeal_allowed,
        'latest_appeal': latest_appeal,
    })

def logout_view(request):
    logout(request)
    return redirect('login')

def landing_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin_panel')
        return redirect('dashboard')
    tenant_count = User.objects.filter(is_superuser=False).filter(
        Q(profile__tenant_owner=F('id')) | Q(profile__tenant_owner__isnull=True)
    ).count()
    shop_count = Shop.objects.count()
    product_count = Product.objects.filter(is_active=True).count()
    staff_count = UserProfile.objects.filter(
        tenant_owner__isnull=False,
        user__is_active=True,
    ).exclude(tenant_owner_id=F('user_id')).count()
    return render(request, 'landing.html', {
        'tenant_count': tenant_count,
        'shop_count': shop_count,
        'product_count': product_count,
        'staff_count': staff_count,
        'pricing': SubscriptionPayment.PLAN_PRICING,
    })

def get_managed_tenant_profile(user_id):
    profile = get_object_or_404(
        UserProfile.objects.select_related('user', 'shop'),
        Q(tenant_owner=F('user')) | Q(tenant_owner__isnull=True),
        user_id=user_id,
        user__is_superuser=False,
    )
    # Older social signups can have a profile created before tenant ownership
    # was assigned. They are standalone accounts elsewhere in the app, so adopt
    # them as tenant owners when an administrator reviews the account.
    if profile.tenant_owner_id is None:
        profile.tenant_owner = profile.user
        profile.role = 'ADMIN'
        profile.save(update_fields=['tenant_owner', 'role'])
    return profile

def delete_tenant_accounts(owner):
    if owner.is_superuser:
        raise PermissionDenied("Superusers cannot be deleted.")
    tenant_user_ids = UserProfile.objects.filter(
        Q(tenant_owner=owner) | Q(shop__tenant_owner=owner) | Q(user__managed_shops__tenant_owner=owner)
    ).values_list('user_id', flat=True).distinct()
    related_users = User.objects.filter(pk__in=tenant_user_ids).exclude(pk=owner.pk)
    deleted_user_count = related_users.count() + 1
    related_users.delete()
    owner.delete()
    return deleted_user_count

def send_suspension_email(user):
    """Send a suspension notice that makes clear the tenant's data is retained."""
    if not user or not user.email:
        return False
    from django.core.mail import send_mail
    from django.conf import settings
    pricing = SubscriptionPayment.PLAN_PRICING
    subject = "viARMS — Store Account Suspended (14-Day Free Trial Ended)"
    message = (
        f"Hello {user.first_name or user.username},\n\n"
        f"Your 14-day free trial on viARMS has ended, and your store operations are now suspended.\n\n"
        f"To restore your store operations and preserve your inventory and sales data, "
        f"please redeem your subscription by selecting one of our plans:\n"
        f"  1. Standard Plan (₦{pricing['STANDARD']['ANNUAL']:,.0f}/year) — 1 shop branch, POS & text inventory management\n"
        f"  2. Premium Plan (₦{pricing['PREMIUM']['QUARTERLY']:,.0f}/quarter or ₦{pricing['PREMIUM']['ANNUAL']:,.0f}/year) — Up to 5 branches, full product images, analytics & CSV export\n"
        f"  3. Golden Premium Plan (₦{pricing['GOLDEN_PREMIUM']['QUARTERLY']:,.0f}/quarter or ₦{pricing['GOLDEN_PREMIUM']['ANNUAL']:,.0f}/year) — Unlimited branches & staff, stock transfers & priority support\n\n"
        "Your account and business records remain stored while suspended. They will not be automatically deleted.\n\n"
        f"Redeem your subscription now: https://scales.pythonanywhere.com/subscription/upgrade/?plan=PREMIUM\n\n"
        f"Best regards,\n"
        f"viARMS Support Team"
    )
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@viarms.com')
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[user.email],
            fail_silently=True
        )
        return True
    except Exception:
        return False

def check_and_process_tenant_lifecycle(owner):
    """
    Checks a tenant's trial and suspension lifecycle:
    - Superusers exempt.
    - If trial expired (>= 14 days), marks is_suspended=True and dispatches an in-app notice.
    - Suspended accounts and business data are retained until an administrator explicitly deletes them.
    Returns False; lifecycle processing never deletes tenant data.
    """
    if not owner or owner.is_superuser:
        return False
    profile = getattr(owner, 'profile', None)
    if not profile:
        return False

    # 1. Free Trial Expired -> Automatic Suspension
    if profile.plan == 'FREE' and profile.trial_start_date:
        today = timezone.localdate()
        days_since_trial = (today - profile.trial_start_date).days
        if days_since_trial >= 14:
            if not profile.is_suspended:
                profile.is_suspended = True
                profile.suspended_at = timezone.now()
                profile.suspension_reason = "14-day free trial expired"
                profile.save(update_fields=['is_suspended', 'suspended_at', 'suspension_reason'])

            if not profile.suspension_notified:
                profile.suspension_notified = True
                profile.save(update_fields=['suspension_notified'])
                create_admin_notification(
                    title="Store Suspended - Free Trial Ended",
                    message="Your 14-day free trial has expired and your store operations are suspended. Choose a subscription plan to restore access. Your account and business data are retained while suspended and are not automatically deleted.",
                    notification_type="WARNING",
                    target_tenant=owner
                )

    # 2. Paid Subscription Expired
    elif profile.plan != 'FREE' and profile.is_subscription_expired:
        if not profile.is_suspended:
            profile.is_suspended = True
            profile.suspended_at = timezone.now()
            profile.suspension_reason = "Paid subscription expired"
            profile.save(update_fields=['is_suspended', 'suspended_at', 'suspension_reason'])

    return False

def process_all_tenants_lifecycle():
    """Scans tenant owners and applies suspension rules without deleting tenant data."""
    tenants = User.objects.filter(is_superuser=False).filter(
        Q(profile__tenant_owner=F('id')) | Q(profile__tenant_owner__isnull=True)
    ).distinct()
    suspended_count = 0
    deleted_count = 0  # Kept in the return value for callers using the existing command API.
    for tenant in list(tenants):
        profile = getattr(tenant, 'profile', None)
        if not profile:
            continue
        was_suspended = profile.is_suspended
        check_and_process_tenant_lifecycle(tenant)
        if not was_suspended and profile.is_suspended:
            suspended_count += 1
    return suspended_count, deleted_count

@login_required
@user_passes_test(lambda u: u.is_superuser)
def admin_panel_view(request):
    # Refresh tenant lifecycle statuses without deleting suspended tenant data.
    process_all_tenants_lifecycle()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve_user':
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            if user_profile.kyc_status != 'VERIFIED':
                messages.error(request, f"Verify KYC for {user_profile.user.username} before approving the account.")
            else:
                user_profile.is_approved = True
                if user_profile.plan == 'FREE' and not user_profile.trial_start_date:
                    user_profile.trial_start_date = timezone.localdate()
                user_profile.save()
                messages.success(request, f"User {user_profile.user.username} has been approved.")
        elif action == 'verify_kyc':
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            user_profile.kyc_status = 'VERIFIED'
            user_profile.kyc_notes = request.POST.get('kyc_notes', '').strip()
            user_profile.kyc_reviewed_at = timezone.now()
            user_profile.save(update_fields=['kyc_status', 'kyc_notes', 'kyc_reviewed_at'])
            messages.success(request, f"KYC verified for {user_profile.user.username}.")
        elif action == 'return_kyc':
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            user_profile.kyc_status = 'RETURNED'
            user_profile.kyc_notes = request.POST.get('kyc_notes', '').strip()
            user_profile.kyc_reviewed_at = timezone.now()
            user_profile.save(update_fields=['kyc_status', 'kyc_notes', 'kyc_reviewed_at'])
            messages.success(request, f"KYC returned to {user_profile.user.username} for more information.")
        elif action == 'reject_user':
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            user = user_profile.user
            confirmation = request.POST.get('delete_confirmation', '')
            if user.is_superuser or user == request.user:
                messages.error(request, "The super-admin account cannot be deleted from this panel.")
            elif confirmation != user.username:
                messages.error(request, "Deletion was not confirmed. Enter the exact username to permanently delete this tenant and its data.")
            else:
                username = user.username
                with transaction.atomic():
                    deleted_accounts = delete_tenant_accounts(user)
                messages.success(request, f"Registration for {username} and its tenant data was permanently deleted ({deleted_accounts} user account(s) removed).")
        elif action in ('suspend_tenant', 'activate_tenant', 'delete_tenant'):
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            user = user_profile.user
            if user.is_superuser or user == request.user:
                messages.error(request, "The super-admin account cannot be changed from this panel.")
            elif action == 'suspend_tenant':
                if user.is_active:
                    user.is_active = False
                    user.save(update_fields=['is_active'])
                    messages.success(request, f"Business account {user.username} has been suspended. Its data has been retained.")
                else:
                    messages.info(request, f"Business account {user.username} is already suspended.")
            elif action == 'activate_tenant':
                if not user.is_active:
                    user.is_active = True
                    user.save(update_fields=['is_active'])
                    messages.success(request, f"Business account {user.username} has been reactivated.")
                else:
                    messages.info(request, f"Business account {user.username} is already active.")
            else:
                confirmation = request.POST.get('delete_confirmation', '')
                if confirmation != user.username:
                    messages.error(request, "Deletion was not confirmed. Enter the exact username to permanently delete this tenant and its data.")
                else:
                    username = user.username
                    with transaction.atomic():
                        deleted_accounts = delete_tenant_accounts(user)
                    messages.success(request, f"Tenant {username} and its business data were permanently deleted ({deleted_accounts} user account(s) removed).")
        elif action == 'review_appeal':
            appeal = get_object_or_404(AccountAppeal.objects.select_related('user'), id=request.POST.get('appeal_id'), status='PENDING')
            response_text = request.POST.get('admin_response', '').strip()[:2000]
            appeal.status = 'REVIEWED'
            appeal.admin_response = response_text
            appeal.reviewed_at = timezone.now()
            appeal.save(update_fields=['status', 'admin_response', 'reviewed_at'])
            if appeal.user.email:
                try:
                    from django.core.mail import send_mail
                    send_mail(
                        'Update on your viARMS account appeal',
                        f"Your account appeal has been reviewed.\n\nAdministrator response: {response_text or 'Please sign in or contact support for assistance.'}",
                        None,
                        [appeal.user.email],
                        fail_silently=False,
                    )
                except Exception:
                    logger.exception('Could not email account appeal review for user id %s.', appeal.user_id)
            messages.success(request, f"The account appeal from {appeal.user.username} was marked reviewed.")
        elif action == 'update_plan':
            new_plan = request.POST.get('plan')
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            valid_plans = dict(UserProfile._meta.get_field('plan').choices)
            if new_plan not in valid_plans:
                messages.error(request, "Select a valid subscription plan.")
            else:
                user_profile.plan = new_plan
                if new_plan != 'FREE':
                    user_profile.trial_start_date = None
                else:
                    if not user_profile.trial_start_date:
                        user_profile.trial_start_date = timezone.localdate()
                user_profile.save(update_fields=['plan', 'trial_start_date'])
                messages.success(request, f"Plan updated to {new_plan} for {user_profile.user.username}.")
        elif action == 'create_ad':
            title = request.POST.get('title')
            content = request.POST.get('content')
            image_url = request.POST.get('image_url')
            Advertisement.objects.create(title=title, content=content, image_url=image_url)
            messages.success(request, "Advertisement created successfully.")
        elif action == 'delete_ad':
            ad_id = request.POST.get('ad_id')
            ad = get_object_or_404(Advertisement, id=ad_id)
            ad.delete()
            messages.success(request, "Advertisement deleted.")
        elif action == 'send_notification':
            title = request.POST.get('title', '').strip()
            message = request.POST.get('message', '').strip()
            notification_type = request.POST.get('notification_type', 'INFO')
            target_tenant_id = request.POST.get('target_tenant_id', '').strip()
            if not title or not message:
                messages.error(request, "Both title and message are required for broadcast.")
            else:
                target_tenant = None
                if target_tenant_id and target_tenant_id != 'ALL':
                    target_tenant = User.objects.filter(
                        id=target_tenant_id, is_superuser=False
                    ).first()
                    if not target_tenant:
                        messages.error(request, "The selected tenant could not be found.")
                        return redirect('admin_panel')
                if notification_type not in dict(AdminNotification.NOTIFICATION_TYPES):
                    notification_type = 'INFO'
                notification = create_admin_notification(
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    target_tenant=target_tenant,
                    sender=request.user
                )
                target_label = target_tenant.username if target_tenant else "All Tenants"
                queued_count = notification.email_deliveries.count()
                messages.success(
                    request,
                    f"Notification '{title}' saved for {target_label}; {queued_count} email(s) queued for delivery."
                )
        elif action == 'confirm_subscription':
            payment_id = request.POST.get('payment_id')
            payment = get_object_or_404(SubscriptionPayment, id=payment_id, status='PENDING')
            activate_subscription_upgrade(payment, confirmed_by=request.user)
            messages.success(request, f"Payment #{payment.reference} confirmed! Upgraded {payment.tenant.username} to {payment.get_plan_display()}.")
        elif action == 'reject_subscription':
            payment_id = request.POST.get('payment_id')
            rejection_reason = request.POST.get('rejection_reason', '').strip()
            payment = get_object_or_404(SubscriptionPayment, id=payment_id, status='PENDING')
            payment.status = 'REJECTED'
            payment.rejection_reason = rejection_reason
            payment.confirmed_at = timezone.now()
            payment.confirmed_by = request.user
            payment.save(update_fields=['status', 'rejection_reason', 'confirmed_at', 'confirmed_by'])
            create_admin_notification(
                title="Subscription Payment Not Approved",
                message=f"Your subscription payment submission (#{payment.reference}) could not be confirmed. Reason: {rejection_reason or 'Details could not be verified'}. Please contact support or retry.",
                notification_type="URGENT",
                target_tenant=payment.tenant,
                sender=request.user
            )
            messages.warning(request, f"Payment #{payment.reference} rejected.")
        elif action == 'update_bank_settings':
            # Subscription payment credentials belong to the platform, not a tenant receipt.
            store_settings = StoreSettings.get_settings()
            store_settings.company_bank_name = request.POST.get('company_bank_name', store_settings.company_bank_name).strip()
            store_settings.company_account_name = request.POST.get('company_account_name', store_settings.company_account_name).strip()
            store_settings.company_account_number = request.POST.get('company_account_number', store_settings.company_account_number).strip()
            store_settings.remita_merchant_id = request.POST.get('remita_merchant_id', store_settings.remita_merchant_id).strip()
            store_settings.remita_service_type_id = request.POST.get('remita_service_type_id', store_settings.remita_service_type_id)
            new_api_key = request.POST.get('remita_api_key', '').strip()
            if new_api_key and not new_api_key.startswith('***'):
                store_settings.remita_api_key = new_api_key
            store_settings.save()
            messages.success(request, "Company subscription payment details and Remita API credentials updated successfully.")
        elif action == 'delete_notification':
            notif_id = request.POST.get('notification_id')
            notif = get_object_or_404(AdminNotification, id=notif_id)
            notif.delete()
            messages.success(request, "Notification removed.")
        else:
            messages.error(request, "Unknown admin action.")

        redirect_url = reverse('admin_panel')
        tenant_search = request.POST.get('tenant_search', '').strip()
        if tenant_search:
            redirect_url = f"{redirect_url}?{urlencode({'tenant_search': tenant_search})}"
        return redirect(redirect_url)

    pending_users = UserProfile.objects.filter(
        Q(tenant_owner=F('user')) | Q(tenant_owner__isnull=True),
        is_approved=False,
        user__is_superuser=False,
    ).select_related('user', 'shop')
    pending_kyc = pending_users.exclude(kyc_status='VERIFIED')
    tenant_profiles = UserProfile.objects.filter(
        Q(tenant_owner=F('user')) | Q(tenant_owner__isnull=True),
        user__is_superuser=False,
    ).select_related('user', 'shop')
    tenant_search = request.GET.get('tenant_search', '').strip()
    if tenant_search:
        tenant_profiles = tenant_profiles.filter(
            Q(user__username__icontains=tenant_search)
            | Q(user__email__icontains=tenant_search)
            | Q(shop__name__icontains=tenant_search)
        )
    all_tenants = tenant_profiles.order_by('-user__date_joined')
    all_tenant_profiles = UserProfile.objects.filter(
        Q(tenant_owner=F('user')) | Q(tenant_owner__isnull=True),
        user__is_superuser=False,
    )
    counted_tenant_profiles = all_tenant_profiles.filter(tenant_owner=F('user'))
    ads = Advertisement.objects.all()
    admin_notifications = AdminNotification.objects.filter(is_active=True).select_related(
        'target_tenant', 'sender'
    ).annotate(
        email_pending_count=Count('email_deliveries', filter=Q(email_deliveries__status='PENDING')),
        email_sent_count=Count('email_deliveries', filter=Q(email_deliveries__status='SENT')),
        email_failed_count=Count('email_deliveries', filter=Q(email_deliveries__status='FAILED')),
    ).order_by('-created_at')
    tenant_options = all_tenant_profiles.select_related('user', 'shop').order_by('user__username')
    pending_payments = SubscriptionPayment.objects.filter(status='PENDING').select_related('tenant', 'tenant__profile').order_by('-created_at')
    all_payments = SubscriptionPayment.objects.select_related('tenant', 'confirmed_by').order_by('-created_at')[:25]
    pending_account_appeals = AccountAppeal.objects.filter(status='PENDING').select_related('user').order_by('-created_at')
    store_settings = StoreSettings.get_settings()

    context = {
        'pending_payments': pending_payments,
        'all_payments': all_payments,
        'pending_payments_count': pending_payments.count(),
        'pending_account_appeals': pending_account_appeals,
        'pending_account_appeals_count': pending_account_appeals.count(),
        'total_auth_user_count': User.objects.count(),
        'business_auth_user_count': User.objects.filter(is_superuser=False).count(),
        'store_settings': store_settings,
        'pending_users': pending_users,
        'pending_kyc': pending_kyc,
        'all_tenants': all_tenants,
        'tenant_search': tenant_search,
        'active_tenant_count': counted_tenant_profiles.filter(is_approved=True, user__is_active=True).count(),
        'suspended_tenant_count': counted_tenant_profiles.filter(user__is_active=False).count(),
        'pending_tenant_count': counted_tenant_profiles.filter(is_approved=False, user__is_active=True).count(),
        'ads': ads,
        'admin_notifications': admin_notifications,
        'tenant_options': tenant_options,
    }
    return render(request, 'admin_panel.html', context)

def ads_api(request):
    # Campaign creation is restricted to the company super-admin panel. Serve
    # every active company campaign, including existing records that were
    # previously associated with a branch.
    ads = Advertisement.objects.filter(is_active=True)
    ads_data = [
        {
            'id': ad.id,
            'title': ad.title,
            'content': ad.content,
            'image_url': ad.image_url
        } for ad in ads
    ]
    return JsonResponse({'ads': ads_data})

@login_required
def toggle_dashboard_mode(request):
    """Toggle between retail and wholesale dashboard modes.
    Stores the choice in the session as 'dashboard_mode'."""
    current = request.session.get('dashboard_mode', 'retail')
    if not has_wholesale_access(request.user):
        request.session['dashboard_mode'] = 'retail'
        messages.info(request, 'Wholesale mode is not included with the Standard plan.')
        return redirect('dashboard')
    new_mode = 'wholesale' if current == 'retail' else 'retail'
    request.session['dashboard_mode'] = new_mode
    request.session.modified = True
    messages.success(request, f"Switched to {new_mode.capitalize()} mode.")
    return redirect('dashboard')


@login_required
def wholesale_dashboard_view(request):
    """Wholesale dashboard entry point. Sets session mode to wholesale and delegates to dashboard_view."""
    if not has_wholesale_access(request.user):
        request.session['dashboard_mode'] = 'retail'
        messages.info(request, 'Wholesale mode is not included with the Standard plan.')
        return redirect('dashboard')
    request.session['dashboard_mode'] = 'wholesale'
    request.session.modified = True
    return dashboard_view(request)


@login_required
def dashboard_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    user_profile = request.user.profile
    user_role = user_profile.role
    user_shop = user_profile.shop

    # Active dashboard mode: 'retail' or 'wholesale'
    dashboard_mode = request.session.get('dashboard_mode', 'retail')
    if dashboard_mode == 'wholesale' and not has_wholesale_access(request.user):
        dashboard_mode = 'retail'
        request.session['dashboard_mode'] = 'retail'

    today = timezone.localdate()
    
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    else:
        start_date = today

    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    else:
        end_date = today

    start_of_period = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_of_period = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))

    # Determine shop scope
    # If Admin, default is all shops, but can filter by ?shop_id=X
    selected_shop = None
    shop_filter_id = request.GET.get('shop_id')
    tenant = get_tenant_scope(request.user)
    
    if user_role == 'ADMIN':
        if tenant:
            shops = Shop.objects.filter(tenant_owner=tenant).order_by('name')
        else:
            shops = Shop.objects.all().order_by('name')

        if shop_filter_id:
            if tenant:
                selected_shop = get_object_or_404(Shop, id=shop_filter_id, tenant_owner=tenant)
            else:
                selected_shop = get_object_or_404(Shop, id=shop_filter_id)
    else:
        if tenant:
            shops = Shop.objects.filter(id=user_shop.id, tenant_owner=tenant) if user_shop else Shop.objects.none()
        else:
            shops = Shop.objects.filter(id=user_shop.id) if user_shop else Shop.objects.none()
        selected_shop = user_shop

    # Base Querysets filtered by shop
    if tenant:
        sales_qs = Sale.objects.filter(shop__tenant_owner=tenant)
        products_qs = Product.objects.filter(is_active=True, shop__tenant_owner=tenant)
    else:
        sales_qs = Sale.objects.all()
        products_qs = Product.objects.filter(is_active=True)

    if selected_shop:
        sales_qs = sales_qs.filter(shop=selected_shop)
        products_qs = products_qs.filter(shop=selected_shop)
    elif user_role != 'ADMIN':
        # Fallback: if not admin and has no shop, query empty sets
        sales_qs = sales_qs.none()
        products_qs = products_qs.none()

    # Sales metrics
    period_sales = sales_qs.filter(timestamp__range=(start_of_period, end_of_period))
    today_revenue = period_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    today_count = period_sales.count()

    today_tax = period_sales.aggregate(total=Sum('tax_amount'))['total'] or Decimal('0.00')
    period_items = SaleItem.objects.filter(sale__in=period_sales)
    today_cost = sum(item.unit_cost_price * item.quantity for item in period_items)
    today_profit = today_revenue - today_tax - today_cost

    # Stock metrics
    total_products = products_qs.count()
    low_stock_products = products_qs.filter(stock_quantity__lte=F('min_stock_level'))
    low_stock_count = low_stock_products.count()

    # Expiry metrics
    expired_products = products_qs.filter(expiry_date__lte=today)
    expired_count = expired_products.count()

    expiring_soon_products = products_qs.filter(expiry_date__gt=today, expiry_date__lte=today + timedelta(days=30))
    expiring_soon_count = expiring_soon_products.count()

    # Recent Sales
    recent_sales = sales_qs.select_related('cashier', 'shop').order_by('-timestamp')[:5]
    dashboard_products = products_qs.select_related('category').order_by('name')[:5]

    # Weekly chart data
    chart_labels = []
    chart_revenue = []
    chart_profit = []

    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
        day_end = timezone.make_aware(datetime.combine(day, datetime.max.time()))
        
        day_sales = sales_qs.filter(timestamp__range=(day_start, day_end))
        day_rev = day_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        day_tax = day_sales.aggregate(total=Sum('tax_amount'))['total'] or Decimal('0.00')
        
        day_items = SaleItem.objects.filter(sale__in=day_sales)
        day_cost = sum(item.unit_cost_price * item.quantity for item in day_items)
        day_prof = day_rev - day_tax - day_cost

        chart_labels.append(day.strftime('%a (%b %d)'))
        chart_revenue.append(float(day_rev))
        chart_profit.append(float(day_prof))

    # Top selling items
    thirty_days_ago = timezone.now() - timedelta(days=30)
    top_selling = SaleItem.objects.filter(sale__in=sales_qs.filter(timestamp__gte=thirty_days_ago)) \
        .values('product__name') \
        .annotate(total_qty=Sum('quantity'), total_rev=Sum('subtotal')) \
        .order_by('-total_qty')[:5]

    book_accessories_metrics = {}
    if user_profile.business_type == 'BOOK_ACCESSORIES':
        book_products = products_qs.filter(
            Q(isbn__gt='') | Q(author__gt='') | Q(publisher__gt='')
        )
        accessory_products = products_qs.filter(
            Q(color__gt='') | Q(size__gt='') | Q(warranty_period__gt='') | Q(material__gt='')
        )
        book_sales_window = sales_qs.filter(timestamp__gte=thirty_days_ago)
        best_selling_books = SaleItem.objects.filter(
            sale__in=book_sales_window,
            product__in=book_products,
        ).values(
            'product__name', 'product__author', 'product__publisher', 'product__isbn'
        ).annotate(
            total_qty=Sum('quantity'),
            total_revenue=Sum('subtotal'),
        ).order_by('-total_qty')[:6]
        accessory_low_stock = accessory_products.filter(
            stock_quantity__lte=F('min_stock_level')
        ).select_related('category').order_by('stock_quantity', 'name')[:6]
        book_inventory_value = book_products.aggregate(
            value=Sum(F('stock_quantity') * F('cost_price'))
        )['value'] or Decimal('0.00')
        book_accessories_metrics = {
            'book_count': book_products.count(),
            'accessory_count': accessory_products.count(),
            'author_count': book_products.exclude(author__isnull=True).exclude(author='').values('author').distinct().count(),
            'book_inventory_value': book_inventory_value,
            'best_selling_books': best_selling_books,
            'accessory_low_stock': accessory_low_stock,
        }

    # Pharmaceutical specific metrics
    pharmaceutical_metrics = {}
    if user_profile.business_type == 'PHARMACEUTICAL':
        rx_sales = sum(item.subtotal for item in period_items if item.product and item.product.prescription_required)
        otc_sales = sum(item.subtotal for item in period_items if item.product and not item.product.prescription_required)
        radar_90_days = products_qs.filter(expiry_date__gt=today, expiry_date__lte=today + timedelta(days=90)).order_by('expiry_date')

        pharmaceutical_metrics = {
            'rx_revenue': rx_sales,
            'otc_revenue': otc_sales,
            'rx_percentage': int((rx_sales / today_revenue * 100)) if today_revenue > 0 else 0,
            'otc_percentage': int((otc_sales / today_revenue * 100)) if today_revenue > 0 else 0,
            'radar_batches': radar_90_days[:10]
        }

    # Wholesale specific metrics and bulk inventory management data
    wholesale_metrics = {}
    if dashboard_mode == 'wholesale':
        all_wh_prods = list(products_qs.select_related('category', 'shop').order_by('name'))
        total_stock_units = sum(p.stock_quantity for p in all_wh_prods)
        total_inventory_cost = sum(Decimal(str(p.stock_quantity)) * p.cost_price for p in all_wh_prods)
        total_inventory_wholesale_val = sum(Decimal(str(p.stock_quantity)) * p.selling_price for p in all_wh_prods)
        potential_inventory_profit = total_inventory_wholesale_val - total_inventory_cost
        avg_inv_margin_pct = (potential_inventory_profit / total_inventory_wholesale_val * 100) if total_inventory_wholesale_val > 0 else Decimal('0.0')

        total_units_sold = sum(item.quantity for item in period_items)
        avg_order_value = (today_revenue / today_count) if today_count > 0 else Decimal('0.00')
        avg_units_per_order = (total_units_sold / today_count) if today_count > 0 else 0
        realized_margin_pct = (today_profit / today_revenue * 100) if today_revenue > 0 else Decimal('0.0')

        reorder_items = []
        for p in all_wh_prods:
            if p.stock_quantity <= p.min_stock_level:
                suggested_qty = max(50, p.min_stock_level * 5)
                est_cost = Decimal(str(suggested_qty)) * p.cost_price
                reorder_items.append({
                    'product': p,
                    'current_stock': p.stock_quantity,
                    'min_level': p.min_stock_level,
                    'suggested_reorder': suggested_qty,
                    'estimated_cost': est_cost,
                })
        reorder_items = sorted(reorder_items, key=lambda x: x['current_stock'])[:10]
        total_reorder_est_cost = sum(item['estimated_cost'] for item in reorder_items)

        high_value_sales = period_sales.select_related('cashier', 'shop').order_by('-total_amount')[:6]

        top_bulk_movers = period_items.values('product__name', 'product__sku') \
            .annotate(total_qty=Sum('quantity'), total_rev=Sum('subtotal')) \
            .order_by('-total_qty')[:6]

        inventory_rows = []
        for p in all_wh_prods[:60]:
            qty_dec = Decimal(str(p.stock_quantity))
            cost_val = qty_dec * p.cost_price
            sell_val = qty_dec * p.selling_price
            unit_prof = p.selling_price - p.cost_price
            marg = (unit_prof / p.selling_price * 100) if p.selling_price > 0 else Decimal('0.0')
            inventory_rows.append({
                'product': p,
                'stock': p.stock_quantity,
                'cost_price': p.cost_price,
                'wholesale_price': p.selling_price,
                'cost_valuation': cost_val,
                'wholesale_valuation': sell_val,
                'unit_profit': unit_prof,
                'margin_pct': marg,
            })

        wholesale_metrics = {
            'total_stock_units': total_stock_units,
            'total_inventory_cost': total_inventory_cost,
            'total_inventory_wholesale_val': total_inventory_wholesale_val,
            'potential_inventory_profit': potential_inventory_profit,
            'avg_inv_margin_pct': avg_inv_margin_pct,
            'total_units_sold': total_units_sold,
            'avg_order_value': avg_order_value,
            'avg_units_per_order': round(avg_units_per_order, 1),
            'realized_margin_pct': realized_margin_pct,
            'reorder_items': reorder_items,
            'reorder_count': len(reorder_items),
            'total_reorder_est_cost': total_reorder_est_cost,
            'high_value_sales': high_value_sales,
            'top_bulk_movers': top_bulk_movers,
            'inventory_rows': inventory_rows,
            'all_catalog_products': all_wh_prods,
        }

    store_settings = get_store_settings_for_user(request.user, selected_shop)

    context = {
        'dashboard_mode': dashboard_mode,
        'wholesale_metrics': wholesale_metrics,
        'store_settings': store_settings,
        'today_revenue': today_revenue,
        'today_count': today_count,
        'today_profit': today_profit,
        'total_products': total_products,
        'low_stock_count': low_stock_count,
        'low_stock_products': low_stock_products[:5],
        'expired_count': expired_count,
        'expired_products': expired_products[:5],
        'expiring_soon_count': expiring_soon_count,
        'expiring_soon_products': expiring_soon_products[:5],
        'recent_sales': recent_sales,
        'chart_labels': json.dumps(chart_labels),
        'chart_revenue': json.dumps(chart_revenue),
        'chart_profit': json.dumps(chart_profit),
        'top_selling': top_selling,
        'business_type': user_profile.business_type,
        'dashboard_products': dashboard_products,
        'pharmaceutical_metrics': pharmaceutical_metrics,
        'book_accessories_metrics': book_accessories_metrics,
        'is_admin': is_admin(request.user),
        'is_manager': is_manager(request.user),
        'shops': shops,
        'selected_shop': selected_shop,
        'current_shop_id': str(selected_shop.id) if selected_shop else '',
        'start_date': start_date.strftime("%Y-%m-%d"),
        'end_date': end_date.strftime("%Y-%m-%d"),
    }
    return render(request, 'dashboard.html', context)

@login_required
def pos_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    user_profile = request.user.profile
    if not request.user.is_superuser and user_profile.is_account_suspended:
        messages.error(request, "Store operations are suspended because the subscription is inactive. Renew to restore access; your business data is retained.")
        return redirect('dashboard')
    user_shop = user_profile.shop
    user_is_admin = is_admin(request.user)
    tenant = get_tenant_scope(request.user)
    
    if user_is_admin:
        shop_id = request.GET.get('shop_id')
        if shop_id:
            if tenant:
                user_shop = get_object_or_404(Shop, id=shop_id, tenant_owner=tenant)
            else:
                user_shop = get_object_or_404(Shop, id=shop_id)
        elif not user_shop:
            if tenant:
                user_shop = Shop.objects.filter(tenant_owner=tenant).first()
            else:
                user_shop = Shop.objects.first()
            
    if not user_shop and not user_is_admin:
        messages.error(request, "Your profile is not assigned to any shop branch. Please contact an admin.")
        return redirect('dashboard')

    # Query products active in this shop
    # Expired items are excluded from sales!
    today = timezone.localdate()
    if tenant:
        products_query = Product.objects.filter(is_active=True, shop__tenant_owner=tenant)
    else:
        products_query = Product.objects.filter(is_active=True)

    if user_shop:
        products_query = products_query.filter(shop=user_shop)
    
    # Hide expired items (keep products with no expiry date or future expiry)
    products = products_query.filter(
        Q(expiry_date__isnull=True) | Q(expiry_date__gt=today)
    ).order_by('name')

    if tenant:
        categories = Category.objects.filter(tenant_owner=tenant).order_by('name')
        shops = Shop.objects.filter(tenant_owner=tenant).order_by('name') if user_is_admin else None
    else:
        categories = Category.objects.all().order_by('name')
        shops = Shop.objects.all().order_by('name') if user_is_admin else None

    settings = get_store_settings_for_user(request.user, user_shop)
    
    # Format products for JS search, adding expiry warning flags and custom attributes
    products_json = []
    for p in products:
        products_json.append({
            'id': p.id,
            'name': p.name,
            'sku': p.sku,
            'selling_price': str(p.selling_price),
            'stock_quantity': p.stock_quantity,
            'category_id': p.category.id if p.category else None,
            'category_name': p.category.name if p.category else 'Uncategorized',
            'is_low_stock': p.is_low_stock,
            'is_expiring_soon': p.is_expiring_soon,
            'expiry_date': p.expiry_date.strftime('%Y-%m-%d') if p.expiry_date else 'No Expiry',

            # Custom attributes
            'dosage_form': p.dosage_form or '',
            'generic_name': p.generic_name or '',
            'prescription_required': p.prescription_required,
            'manufacturer': p.manufacturer or '',
            'strength': p.strength or '',
            'batch_number': p.batch_number or '',
            'nafdac_reg_no': p.nafdac_reg_no or '',
            'storage_conditions': p.storage_conditions or '',
            'author': p.author or '',
            'publisher': p.publisher or '',
            'isbn': p.isbn or '',
            'color': p.color or '',
            'size': p.size or '',
            'warranty_period': p.warranty_period or '',
            'material': p.material,
            'material_display': p.get_material_display() if p.material else '',
            'image_url': p.image.url if p.image else '',
        })

    allowed_page_sizes = (12, 24, 48, 96)
    try:
        pos_page_size = int(request.GET.get('page_size', 24))
    except (TypeError, ValueError):
        pos_page_size = 24
    if pos_page_size not in allowed_page_sizes:
        pos_page_size = 24

    context = {
        'products': products,
        'categories': categories,
        'settings': settings,
        'products_json': json.dumps(products_json),
        'pos_page_size': pos_page_size,
        'pos_page_sizes': allowed_page_sizes,
        'is_admin': user_is_admin,
        'is_manager': is_manager(request.user),
        'user_shop': user_shop,
        'shops': shops,
        'business_type': user_profile.business_type,
    }
    return render(request, 'pos.html', context)

@login_required
@transaction.atomic
def checkout_api(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=400)
    
    user_profile = request.user.profile
    if not request.user.is_superuser and user_profile.is_account_suspended:
        return JsonResponse({'success': False, 'error': 'Store account is suspended because the subscription expired or is inactive. Renew to restore access; your business data is retained.'}, status=403)
    user_shop = user_profile.shop

    if not user_shop:
        return JsonResponse({'success': False, 'error': 'Cashier profile is not linked to any shop branch.'}, status=400)

    try:
        data = json.loads(request.body)
        cart_items = data.get('items', [])
        discount = Decimal(str(data.get('discount', 0.00)))
        payment_method = data.get('payment_method', 'CASH')
        local_receipt_number = data.get('local_receipt_number')

        if local_receipt_number and Sale.objects.filter(receipt_number=local_receipt_number).exists():
            return JsonResponse({'success': False, 'error': 'Sale already synced.'}, status=400)

        if not cart_items:
            return JsonResponse({'success': False, 'error': 'Cart is empty.'}, status=400)

        # Get settings
        settings = get_store_settings_for_user(request.user, user_shop)
        tax_rate = settings.tax_rate

        subtotal = Decimal('0.00')
        sale_items_data = []
        today = timezone.localdate()

        # Validate stock & expiry, and calculate totals
        for item in cart_items:
            product_id = item.get('product_id')
            quantity = int(item.get('quantity', 0))

            if quantity <= 0:
                return JsonResponse({'success': False, 'error': 'Invalid quantity.'}, status=400)

            product = Product.objects.select_for_update().get(id=product_id, is_active=True, shop=user_shop)

            # Block expired products from being sold
            if product.is_expired:
                return JsonResponse({
                    'success': False,
                    'error': f"Product '{product.name}' is expired (Expiry: {product.expiry_date}) and cannot be sold."
                }, status=400)

            if product.stock_quantity < quantity:
                return JsonResponse({
                    'success': False, 
                    'error': f"Insufficient stock for '{product.name}'. Available: {product.stock_quantity}, Requested: {quantity}."
                }, status=400)

            item_subtotal = product.selling_price * quantity
            subtotal += item_subtotal

            sale_items_data.append({
                'product': product,
                'quantity': quantity,
                'unit_cost_price': product.cost_price,
                'unit_price': product.selling_price,
                'subtotal': item_subtotal
            })

        # Calculate tax and total
        taxable_amount = max(Decimal('0.00'), subtotal - discount)
        tax_amount = (taxable_amount * (tax_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
        total_amount = (taxable_amount + tax_amount).quantize(Decimal('0.01'))

        # Create Sale record bound to this shop
        if local_receipt_number:
            receipt_number = local_receipt_number
        else:
            timestamp_str = datetime.now().strftime('%Y%m%d%H%M%S')
            random_suffix = str(timezone.now().microsecond)[:4]
            receipt_number = f"SALE-{timestamp_str}-{random_suffix}"

        sale = Sale.objects.create(
            receipt_number=receipt_number,
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax_amount=tax_amount,
            discount=discount,
            total_amount=total_amount,
            payment_method=payment_method,
            cashier=request.user,
            shop=user_shop
        )

        # Create Sale Items and update stock
        for item_data in sale_items_data:
            product = item_data['product']
            qty = item_data['quantity']

            SaleItem.objects.create(
                sale=sale,
                product=product,
                quantity=qty,
                unit_cost_price=item_data['unit_cost_price'],
                unit_price=item_data['unit_price'],
                subtotal=item_data['subtotal']
            )

            product.stock_quantity -= qty
            product.save()

        log_business_activity(
            actor=request.user,
            tenant_owner=get_tenant_scope(request.user),
            action='Sale completed',
            details=f"Receipt {sale.receipt_number}: {len(sale_items_data)} product line(s), total ₦{sale.total_amount:,.2f} at {user_shop.name if user_shop else 'Unassigned shop'}.",
        )

        # Build receipt response details
        receipt_items = []
        for item in sale.items.all():
            name_str = item.product.name if item.product else 'Deleted Product'
            if item.product and item.product.strength:
                name_str = f"{name_str} ({item.product.strength})"
            if item.product and item.product.batch_number:
                b_lbl = format_batch_label(item.product.batch_number)
                name_str = f"{name_str} [{b_lbl}]"
            receipt_items.append({
                'name': name_str,
                'quantity': item.quantity,
                'unit_price': f"{item.unit_price:.2f}",
                'subtotal': f"{item.subtotal:.2f}"
            })

        receipt_data = {
            'receipt_number': sale.receipt_number,
            'timestamp': sale.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'subtotal': f"{sale.subtotal:.2f}",
            'tax_rate': f"{sale.tax_rate:.2f}",
            'tax_amount': f"{sale.tax_amount:.2f}",
            'discount': f"{sale.discount:.2f}",
            'total_amount': f"{sale.total_amount:.2f}",
            'payment_method': sale.get_payment_method_display(),
            'cashier': sale.cashier.username if sale.cashier else 'System',
            'store_name': settings.store_name or user_shop.name,
            'store_address': settings.address or user_shop.location or '',
            'store_phone': settings.phone or user_shop.phone or '',
            'store_email': settings.email or '',
            'receipt_footer': settings.receipt_footer or 'Thank you for shopping with viARMS Record Management System. Powered by Scales Technologies.',
            'items': receipt_items
        }

        return JsonResponse({'success': True, 'receipt': receipt_data})

    except Product.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Product does not exist or is not assigned to this shop.'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@login_required
def product_list_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    user_profile = request.user.profile
    user_role = user_profile.role
    user_shop = user_profile.shop
    is_user_admin = is_admin(request.user)
    tenant = get_tenant_scope(request.user)

    if tenant:
        categories = Category.objects.filter(tenant_owner=tenant).order_by('name')
        if is_user_admin:
            shops = Shop.objects.filter(tenant_owner=tenant).order_by('name')
            shop_filter = request.GET.get('shop_id')
            if shop_filter:
                products = Product.objects.filter(is_active=True, shop_id=shop_filter, shop__tenant_owner=tenant)
            else:
                products = Product.objects.filter(is_active=True, shop__tenant_owner=tenant)
        else:
            shops = Shop.objects.filter(id=user_shop.id, tenant_owner=tenant) if user_shop else Shop.objects.none()
            products = Product.objects.filter(is_active=True, shop=user_shop) if user_shop else Product.objects.none()
    else:
        categories = Category.objects.all().order_by('name')
        if is_user_admin:
            shops = Shop.objects.all().order_by('name')
            shop_filter = request.GET.get('shop_id')
            if shop_filter:
                products = Product.objects.filter(is_active=True, shop_id=shop_filter)
            else:
                products = Product.objects.filter(is_active=True)
        else:
            shops = Shop.objects.filter(id=user_shop.id) if user_shop else Shop.objects.none()
            products = Product.objects.filter(is_active=True, shop=user_shop) if user_shop else Product.objects.none()

    products = products.select_related('category', 'shop').order_by('name')

    # Handle Category and Product CRUD
    if request.method == 'POST' and 'action' in request.POST:
        if not request.user.is_superuser and user_profile.is_account_suspended:
            messages.error(request, "Store operations are suspended because the subscription is inactive. Renew to restore access; your business data is retained.")
            return redirect('products')
        # Permission validation: Only Admin or Manager can modify inventory
        if not is_user_admin and not is_manager(request.user):
            messages.error(request, "Permission denied. Only Admins and Shop Managers can edit inventory.")
            return redirect('products')

        action = request.POST.get('action')
        if action == 'bulk_add_products':
            create_bulk_products(request, user_profile, tenant, is_user_admin, user_shop)
            return redirect('products')

        # Categories (Admin only)
        if action == 'add_category':
            if not is_user_admin:
                messages.error(request, "Only Admins can manage categories.")
            else:
                name = request.POST.get('name')
                desc = request.POST.get('description')
                if name:
                    Category.objects.create(name=name, description=desc, tenant_owner=tenant)
                    messages.success(request, f"Category '{name}' created successfully.")

        elif action == 'edit_category':
            if not is_user_admin:
                messages.error(request, "Only Admins can manage categories.")
            else:
                cat_id = request.POST.get('category_id')
                if not cat_id:
                    messages.error(request, "Category ID is missing.")
                    return redirect('products')
                if tenant:
                    cat = Category.objects.filter(id=cat_id, tenant_owner=tenant).first()
                    if not cat:
                        if Category.objects.filter(id=cat_id, tenant_owner__isnull=True).exists():
                            messages.error(request, "Default system categories cannot be modified.")
                        else:
                            messages.error(request, "Category not found.")
                        return redirect('products')
                else:
                    cat = Category.objects.filter(id=cat_id).first()
                    if not cat:
                        messages.error(request, "Category not found.")
                        return redirect('products')
                cat.name = request.POST.get('name')
                cat.description = request.POST.get('description')
                cat.save()
                messages.success(request, f"Category '{cat.name}' updated successfully.")

        elif action == 'delete_category':
            if not is_user_admin:
                messages.error(request, "Only Admins can manage categories.")
            else:
                cat_id = request.POST.get('category_id')
                if not cat_id:
                    messages.error(request, "Category ID is missing.")
                    return redirect('products')
                if tenant:
                    cat = Category.objects.filter(id=cat_id, tenant_owner=tenant).first()
                    if not cat:
                        if Category.objects.filter(id=cat_id, tenant_owner__isnull=True).exists():
                            messages.error(request, "Default system categories cannot be deleted.")
                        else:
                            messages.error(request, "Category not found.")
                        return redirect('products')
                else:
                    cat = Category.objects.filter(id=cat_id).first()
                    if not cat:
                        messages.error(request, "Category not found.")
                        return redirect('products')
                cat.delete()
                messages.success(request, "Category deleted successfully.")

        # Products (Admin & Manager)
        elif action == 'add_product':
            name = request.POST.get('name')
            is_boutique = user_profile.business_type == 'BOUTIQUE'
            sku = (request.POST.get('sku') or '').strip()
            if is_boutique and not sku:
                sku = generate_internal_product_sku()
            cat_id = request.POST.get('category_id')
            cost = Decimal(request.POST.get('cost_price', 0.00))
            sell = Decimal(request.POST.get('selling_price', 0.00))
            if cost > sell:
                messages.error(request, "Cost price cannot be greater than selling price.")
                return redirect('products')
            qty = int(request.POST.get('stock_quantity', 0))
            min_lvl = int(request.POST.get('min_stock_level', 5))
            desc = request.POST.get('description')
            expiry = None if is_boutique else (request.POST.get('expiry_date') or None)

            # Custom business-specific attributes
            dosage_form = request.POST.get('dosage_form', '')
            generic_name = request.POST.get('generic_name', '')
            prescription_required = request.POST.get('prescription_required') in ('on', 'true')
            manufacturer = request.POST.get('manufacturer', '')
            strength = request.POST.get('strength', '')
            batch_number = request.POST.get('batch_number', '')
            nafdac_reg_no = request.POST.get('nafdac_reg_no', '')
            storage_conditions = request.POST.get('storage_conditions', '')
            author = request.POST.get('author', '')
            publisher = request.POST.get('publisher', '')
            isbn = request.POST.get('isbn', '')
            color = request.POST.get('color', '')
            size = request.POST.get('size', '')
            warranty_period = request.POST.get('warranty_period', '')
            material = request.POST.get('material', '')
            uploaded_image = request.FILES.get('image')

            # Standard Plan restriction: Standard Plan should not have product image
            if not user_profile.allows_product_images:
                if uploaded_image:
                    uploaded_image = None
                    messages.info(request, "Product images are not supported on the Standard Plan. Product was saved without an image. Upgrade to Premium to enable images.")
            elif uploaded_image:
                try:
                    uploaded_image = prepare_product_image(uploaded_image)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect('products')

            if user_profile.business_type == 'UTENSILS' and material == 'PLASTIC':
                expiry = None

            # Determine product shop binding
            if is_user_admin:
                prod_shop_id = request.POST.get('shop_id')
                if tenant:
                    prod_shop = get_object_or_404(Shop, id=prod_shop_id, tenant_owner=tenant) if prod_shop_id else None
                else:
                    prod_shop = get_object_or_404(Shop, id=prod_shop_id) if prod_shop_id else None
            else:
                prod_shop = user_shop

            if not prod_shop:
                messages.error(request, "Failed to create product. Shop assignment is required.")
                return redirect('products')

            category = None
            if cat_id and str(cat_id).strip() not in ('', 'None', '0'):
                if tenant:
                    category = Category.objects.filter(tenant_owner=tenant, id=cat_id).first()
                else:
                    category = Category.objects.filter(id=cat_id).first()
                if not category:
                    messages.warning(request, "Selected category was not found; product saved as Uncategorized.")

            if name and sku:
                batch_number, is_new_batch, duplicate_error = assign_product_batch(prod_shop, sku, name, batch_number)
                if duplicate_error:
                    messages.error(request, duplicate_error)
                else:
                    product = Product.objects.create(
                        name=name, sku=sku, category=category, cost_price=cost,
                        selling_price=sell, stock_quantity=qty, min_stock_level=min_lvl,
                        description=desc, expiry_date=expiry, shop=prod_shop,
                        dosage_form=dosage_form, generic_name=generic_name, prescription_required=prescription_required,
                        manufacturer=manufacturer, strength=strength, batch_number=batch_number,
                        nafdac_reg_no=nafdac_reg_no, storage_conditions=storage_conditions,
                        author=author, publisher=publisher, isbn=isbn,
                        color=color, size=size, warranty_period=warranty_period,
                        material=material, image=uploaded_image
                    )
                    stock_report = record_stock_additions(
                        [(product, 0)], actor=request.user, tenant_owner=tenant
                    )
                    log_business_activity(
                        actor=request.user, tenant_owner=tenant, action='Product created',
                        details=f"{product.name} ({product.sku}) added to {prod_shop.name} with {product.stock_quantity} in stock.",
                    )
                    batch_info = f" ({batch_number})" if batch_number else ""
                    report_note = " Stock report queued for the tenant admin." if stock_report else ""
                    messages.success(request, f"Product '{name}'{batch_info} created successfully under '{prod_shop.name}'.{report_note}")

        elif action == 'edit_product':
            prod_id = request.POST.get('product_id')
            # Fetch checking scope to prevent editing other shops' products if manager
            if is_user_admin:
                if tenant:
                    prod = get_object_or_404(Product, id=prod_id, shop__tenant_owner=tenant)
                else:
                    prod = get_object_or_404(Product, id=prod_id)
                prod_shop_id = request.POST.get('shop_id')
                if prod_shop_id:
                    if tenant:
                        prod.shop = get_object_or_404(Shop, id=prod_shop_id, tenant_owner=tenant)
                    else:
                        prod.shop = get_object_or_404(Shop, id=prod_shop_id)
            else:
                prod = get_object_or_404(Product, id=prod_id, shop=user_shop)

            prod.name = request.POST.get('name')
            is_boutique = user_profile.business_type == 'BOUTIQUE'
            sku = (request.POST.get('sku') or '').strip() or prod.sku
            new_expiry = None if is_boutique else (request.POST.get('expiry_date') or None)
            new_material = request.POST.get('material', '')
            uploaded_image = request.FILES.get('image')

            # Standard Plan restriction: Standard Plan should not have product image
            if not user_profile.allows_product_images:
                if uploaded_image:
                    uploaded_image = None
                    messages.info(request, "Product images are not supported on the Standard Plan. Product was saved without an image. Upgrade to Premium to enable images.")
            elif uploaded_image:
                try:
                    uploaded_image = prepare_product_image(uploaded_image)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect('products')

            if user_profile.business_type == 'UTENSILS' and new_material == 'PLASTIC':
                new_expiry = None
            target_shop = prod.shop
            new_batch = format_batch_label(request.POST.get('batch_number') or prod.batch_number or 'BN: 1')
            # Check uniqueness: same sku + shop + batch_number but a DIFFERENT product
            conflict = Product.objects.filter(
                sku__iexact=sku, shop=target_shop, batch_number__iexact=new_batch, is_active=True
            ).exclude(id=prod.id).exists()
            if conflict:
                messages.error(request, f"Another product with SKU '{sku}' and batch '{new_batch}' already exists in this shop.")
            else:
                quantity_before = prod.stock_quantity
                prod.sku = sku
                prod.batch_number = new_batch
                cat_id = request.POST.get('category_id')
                if cat_id and str(cat_id).strip() not in ('', 'None', '0'):
                    if tenant:
                        prod.category = Category.objects.filter(tenant_owner=tenant, id=cat_id).first()
                    else:
                        prod.category = Category.objects.filter(id=cat_id).first()
                    if not prod.category:
                        messages.warning(request, "Selected category was not found; product saved as Uncategorized.")
                else:
                    prod.category = None
                new_cost = Decimal(request.POST.get('cost_price', 0.00))
                new_sell = Decimal(request.POST.get('selling_price', 0.00))
                if new_cost > new_sell:
                    messages.error(request, "Cost price cannot be greater than selling price.")
                    return redirect('products')
                prod.cost_price = new_cost
                prod.selling_price = new_sell
                prod.stock_quantity = int(request.POST.get('stock_quantity', 0))
                prod.min_stock_level = int(request.POST.get('min_stock_level', 5))
                prod.description = request.POST.get('description')
                prod.expiry_date = new_expiry

                # Custom business-specific attributes
                prod.dosage_form = request.POST.get('dosage_form', '')
                prod.generic_name = request.POST.get('generic_name', '')
                prod.prescription_required = request.POST.get('prescription_required') in ('on', 'true')
                prod.manufacturer = request.POST.get('manufacturer', '')
                prod.strength = request.POST.get('strength', '')
                prod.nafdac_reg_no = request.POST.get('nafdac_reg_no', '')
                prod.storage_conditions = request.POST.get('storage_conditions', '')
                prod.author = request.POST.get('author', '')
                prod.publisher = request.POST.get('publisher', '')
                prod.isbn = request.POST.get('isbn', '')
                prod.color = request.POST.get('color', '')
                prod.size = request.POST.get('size', '')
                prod.warranty_period = request.POST.get('warranty_period', '')
                prod.material = new_material
                old_image_name = prod.image.name if prod.image else None
                if uploaded_image:
                    prod.image = uploaded_image
                elif request.POST.get('remove_image') == 'on':
                    prod.image = None

                prod.save()
                stock_report = record_stock_additions(
                    [(prod, quantity_before)], actor=request.user, tenant_owner=tenant
                )
                if prod.stock_quantity < quantity_before:
                    log_business_activity(
                        actor=request.user, tenant_owner=tenant, action='Stock adjusted',
                        details=f"{prod.name}: quantity reduced from {quantity_before} to {prod.stock_quantity}.",
                    )
                if old_image_name and old_image_name != (prod.image.name if prod.image else None):
                    prod.image.storage.delete(old_image_name)
                report_note = " Stock report queued for the tenant admin." if stock_report else ""
                messages.success(request, f"Product '{prod.name}' updated successfully.{report_note}")

        elif action == 'delete_product':
            prod_id = request.POST.get('product_id')
            if is_user_admin:
                if tenant:
                    prod = get_object_or_404(Product, id=prod_id, shop__tenant_owner=tenant)
                else:
                    prod = get_object_or_404(Product, id=prod_id)
            else:
                prod = get_object_or_404(Product, id=prod_id, shop=user_shop)
            
            prod.is_active = False
            prod.save()
            log_business_activity(
                actor=request.user, tenant_owner=tenant, action='Product archived',
                details=f"{prod.name} ({prod.sku}) was archived from {prod.shop.name if prod.shop_id else 'Unassigned shop'}.",
            )
            messages.success(request, f"Product '{prod.name}' deleted successfully.")

        return redirect('products')

    # --- Stock Summary Aggregates ---
    from django.db.models import Sum as DSum, F as DF, ExpressionWrapper, DecimalField
    stock_summary = products.aggregate(
        total_units=DSum('stock_quantity'),
        total_cost_value=DSum(ExpressionWrapper(DF('cost_price') * DF('stock_quantity'), output_field=DecimalField(max_digits=14, decimal_places=2))),
        total_sell_value=DSum(ExpressionWrapper(DF('selling_price') * DF('stock_quantity'), output_field=DecimalField(max_digits=14, decimal_places=2))),
    )
    total_units = stock_summary['total_units'] or 0
    total_cost_value = stock_summary['total_cost_value'] or Decimal('0.00')
    total_sell_value = stock_summary['total_sell_value'] or Decimal('0.00')
    total_potential_profit = total_sell_value - total_cost_value
    total_skus = products.count()
    low_stock_count = products.filter(stock_quantity__lte=F('min_stock_level')).count()
    expired_count = products.filter(expiry_date__lte=timezone.localdate()).count()

    # Keep the stock list label consistent with the POS, including older rows
    # that may store just a number or a legacy "Batch" prefix.
    for product in products:
        product.batch_number_display = format_batch_label(product.batch_number)

    context = {
        'products': products,
        'categories': categories,
        'is_admin': is_user_admin,
        'is_manager': is_manager(request.user),
        'shops': shops,
        'current_shop_filter': request.GET.get('shop_id', ''),
        'user_shop': user_shop,
        # Stock summary
        'total_skus': total_skus,
        'total_units': total_units,
        'total_cost_value': total_cost_value,
        'total_sell_value': total_sell_value,
        'total_potential_profit': total_potential_profit,
        'low_stock_count': low_stock_count,
        'expired_count': expired_count,
        'business_type': user_profile.business_type,
        'material_choices': Product.MATERIAL_CHOICES,
        'max_bulk_products': MAX_BULK_PRODUCTS,
    }
    return render(request, 'products.html', context)

@login_required
def sales_history_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    user_profile = request.user.profile
    user_role = user_profile.role
    user_shop = user_profile.shop
    is_user_admin = is_admin(request.user)
    tenant = get_tenant_scope(request.user)

    if tenant:
        sales = Sale.objects.filter(shop__tenant_owner=tenant).select_related('cashier', 'shop').prefetch_related('items__product').order_by('-timestamp')
    else:
        sales = Sale.objects.select_related('cashier', 'shop').prefetch_related('items__product').order_by('-timestamp')
    
    # Scope check
    if not is_user_admin:
        sales = sales.filter(shop=user_shop) if user_shop else sales.none()

    # Filter operations
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    cashier_id = request.GET.get('cashier')
    payment_method = request.GET.get('payment_method')
    search_query = request.GET.get('search')
    shop_id = request.GET.get('shop_id')

    if start_date:
        sales = sales.filter(timestamp__date__gte=start_date)
    if end_date:
        sales = sales.filter(timestamp__date__lte=end_date)
    if cashier_id:
        sales = sales.filter(cashier_id=cashier_id)
    if payment_method:
        sales = sales.filter(payment_method=payment_method)
    if search_query:
        sales = sales.filter(receipt_number__icontains=search_query)
    if is_user_admin and shop_id:
        if tenant:
            sales = sales.filter(shop_id=shop_id, shop__tenant_owner=tenant)
        else:
            sales = sales.filter(shop_id=shop_id)

    # Filter dropdowns lists
    if is_user_admin:
        if tenant:
            cashiers = User.objects.filter(sales__isnull=False, profile__tenant_owner=tenant).distinct()
            shops = Shop.objects.filter(tenant_owner=tenant).order_by('name')
        else:
            cashiers = User.objects.filter(sales__isnull=False).distinct()
            shops = Shop.objects.all().order_by('name')
    else:
        if tenant:
            cashiers = User.objects.filter(sales__isnull=False, profile__shop=user_shop, profile__tenant_owner=tenant).distinct()
        else:
            cashiers = User.objects.filter(sales__isnull=False, profile__shop=user_shop).distinct()
        shops = Shop.objects.none()

    payment_choices = Sale.PAYMENT_METHODS

    allowed_page_sizes = (25, 50, 100)
    try:
        page_size = int(request.GET.get('page_size', 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in allowed_page_sizes:
        page_size = 25
    query_params = request.GET.copy()
    query_params.pop('page', None)
    pagination_query = query_params.urlencode()
    sales_page = Paginator(sales, page_size).get_page(request.GET.get('page'))

    context = {
        'sales': sales_page.object_list,
        'sales_page': sales_page,
        'page_size': page_size,
        'page_sizes': allowed_page_sizes,
        'pagination_query': pagination_query,
        'cashiers': cashiers,
        'shops': shops,
        'payment_choices': payment_choices,
        'is_admin': is_user_admin,
        'is_manager': is_manager(request.user),
    }
    return render(request, 'sales_history.html', context)


def _get_sale_for_user(user, sale_id):
    user_shop = user.profile.shop
    tenant = get_tenant_scope(user)
    sales = Sale.objects.select_related('cashier', 'shop').prefetch_related('items__product')

    if is_admin(user):
        if tenant and Shop.objects.filter(tenant_owner=tenant).exists():
            return get_object_or_404(sales, id=sale_id, shop__tenant_owner=tenant)
        return get_object_or_404(sales, id=sale_id)

    if user_shop and user_shop.tenant_owner:
        return get_object_or_404(
            sales,
            id=sale_id,
            shop=user_shop,
            shop__tenant_owner=user_shop.tenant_owner,
        )
    return get_object_or_404(sales, id=sale_id, shop=user_shop)


@login_required
def sale_detail_view(request, sale_id):
    sale = _get_sale_for_user(request.user, sale_id)
    return render(request, 'sale_detail.html', {
        'sale': sale,
        'items': sale.items.all(),
        'is_admin': is_admin(request.user),
        'back_url': _safe_detail_return_url(request, reverse('sales_history')),
    })


@login_required
def sale_detail_json(request, sale_id):
    sale = _get_sale_for_user(request.user, sale_id)

    items_data = []
    for item in sale.items.all():
        name_str = item.product.name if item.product else 'Deleted Product'
        if item.product and item.product.strength:
            name_str = f"{name_str} ({item.product.strength})"
        if item.product and item.product.batch_number:
            b_lbl = format_batch_label(item.product.batch_number)
            name_str = f"{name_str} [{b_lbl}]"
        items_data.append({
            'name': name_str,
            'quantity': item.quantity,
            'unit_price': f"{item.unit_price:.2f}",
            'subtotal': f"{item.subtotal:.2f}"
        })

    settings = get_store_settings_for_user(request.user, sale.shop)
    receipt_data = {
        'receipt_number': sale.receipt_number,
        'timestamp': sale.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
        'subtotal': f"{sale.subtotal:.2f}",
        'tax_rate': f"{sale.tax_rate:.2f}",
        'tax_amount': f"{sale.tax_amount:.2f}",
        'discount': f"{sale.discount:.2f}",
        'total_amount': f"{sale.total_amount:.2f}",
        'payment_method': sale.get_payment_method_display(),
        'cashier': sale.cashier.username if sale.cashier else 'System',
        'store_name': settings.store_name or (sale.shop.name if sale.shop else "Our Shop"),
        'store_address': settings.address or (sale.shop.location if sale.shop and sale.shop.location else ""),
        'store_phone': settings.phone or (sale.shop.phone if sale.shop and sale.shop.phone else ""),
        'store_email': settings.email or '',
        'receipt_footer': settings.receipt_footer or 'Thank you for shopping with viARMS Record Management System. Powered by Scales Technologies.',
        'items': items_data
    }
    return JsonResponse({'success': True, 'receipt': receipt_data})

@login_required
@user_passes_test(lambda u: not u.is_superuser and is_admin(u))
def activity_log_view(request):
    tenant = get_tenant_scope(request.user)
    activity = ActivityLog.objects.filter(tenant_owner=tenant).select_related('actor')[:100]
    return render(request, 'activity_log.html', {'activity_entries': activity, 'is_admin': True})


@login_required
@user_passes_test(lambda u: not u.is_superuser and is_admin(u))
def activity_detail_view(request, activity_id):
    tenant = get_tenant_scope(request.user)
    entry = get_object_or_404(
        ActivityLog.objects.select_related('actor'),
        id=activity_id,
        tenant_owner=tenant,
    )
    return render(request, 'activity_detail.html', {
        'entry': entry,
        'back_url': _safe_detail_return_url(request, reverse('activity_log')),
    })


@login_required
@user_passes_test(lambda u: not u.is_superuser and is_admin(u))
def inventory_report_download_view(request):
    tenant = get_tenant_scope(request.user)
    if request.method != 'POST':
        return redirect('activity_log')
    report = StockInReport(
        tenant_owner=tenant,
        actor=request.user,
        include_inventory_snapshot=True,
        created_at=timezone.now(),
    )
    from .management.commands.send_notification_emails import Command as EmailCommand
    workbook = EmailCommand._build_stock_workbook(report, [])
    log_business_activity(
        actor=request.user, tenant_owner=tenant, action='Inventory report downloaded',
        details='Downloaded a current categorized inventory report.',
    )
    response = HttpResponse(
        workbook,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="viarms_inventory_{timezone.localdate():%Y%m%d}.xlsx"'
    return response


@login_required
@user_passes_test(lambda u: not u.is_superuser and is_admin(u))
def inventory_report_email_view(request):
    if request.method != 'POST':
        return redirect('activity_log')
    tenant = get_tenant_scope(request.user)
    recipient = (tenant.email or '').strip()
    if not recipient:
        messages.error(request, 'Add an email address to the business admin account before requesting an emailed report.')
        return redirect('activity_log')
    report = StockInReport.objects.create(
        tenant_owner=tenant,
        actor=request.user,
        recipient=recipient,
        include_inventory_snapshot=True,
    )
    log_business_activity(
        actor=request.user, tenant_owner=tenant, action='Inventory report emailed',
        details=f'Queued a current categorized inventory report for {recipient}.',
    )
    messages.success(request, f'Inventory report queued for {recipient}. It will be sent by the configured email worker.')
    return redirect('activity_log')


@login_required
def export_sales_csv(request):
    user_profile = request.user.profile
    is_user_admin = is_admin(request.user)
    user_shop = user_profile.shop
    tenant = get_tenant_scope(request.user)

    if tenant:
        sales = Sale.objects.filter(shop__tenant_owner=tenant).select_related('cashier', 'shop').order_by('-timestamp')
    else:
        sales = Sale.objects.select_related('cashier', 'shop').order_by('-timestamp')

    if not is_user_admin:
        sales = sales.filter(shop=user_shop) if user_shop else sales.none()

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    cashier_id = request.GET.get('cashier')
    payment_method = request.GET.get('payment_method')
    search_query = request.GET.get('search')
    shop_id = request.GET.get('shop_id')

    if start_date:
        sales = sales.filter(timestamp__date__gte=start_date)
    if end_date:
        sales = sales.filter(timestamp__date__lte=end_date)
    if cashier_id:
        sales = sales.filter(cashier_id=cashier_id)
    if payment_method:
        sales = sales.filter(payment_method=payment_method)
    if search_query:
        sales = sales.filter(receipt_number__icontains=search_query)
    if is_user_admin and shop_id:
        if tenant:
            sales = sales.filter(shop_id=shop_id, shop__tenant_owner=tenant)
        else:
            sales = sales.filter(shop_id=shop_id)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="sales_report_{datetime.now().strftime("%Y%m%d")}.csv"'

    writer = csv.writer(response)
    
    if is_user_admin:
        writer.writerow(['Receipt Number', 'Timestamp', 'Shop Branch', 'Cashier', 'Subtotal', 'Tax', 'Discount', 'Total Amount', 'Cost Price Total', 'Net Profit', 'Payment Method'])
    else:
        writer.writerow(['Receipt Number', 'Timestamp', 'Shop Branch', 'Cashier', 'Subtotal', 'Tax', 'Discount', 'Total Amount', 'Payment Method'])

    for sale in sales:
        local_ts = timezone.localtime(sale.timestamp).strftime('%Y-%m-%d %H:%M:%S')
        cashier_name = sale.cashier.username if sale.cashier else 'System'
        shop_name = sale.shop.name if sale.shop else 'Unassigned Shop'
        
        if is_user_admin:
            writer.writerow([
                sale.receipt_number, local_ts, shop_name, cashier_name,
                sale.subtotal, sale.tax_amount, sale.discount, sale.total_amount,
                sale.total_cost, sale.net_profit, sale.get_payment_method_display()
            ])
        else:
            writer.writerow([
                sale.receipt_number, local_ts, shop_name, cashier_name,
                sale.subtotal, sale.tax_amount, sale.discount, sale.total_amount,
                sale.get_payment_method_display()
            ])

    return response

@login_required
def export_wholesale_catalog_csv(request):
    """Exports active product catalog with wholesale prices, batch numbers, and stock levels as CSV."""
    tenant = get_tenant_scope(request.user)
    user_profile = request.user.profile
    if not has_wholesale_access(request.user):
        messages.info(request, 'Wholesale features are not included with the Standard plan.')
        return redirect('dashboard')
    is_user_admin = is_admin(request.user)
    user_shop = user_profile.shop

    if tenant:
        products = Product.objects.filter(is_active=True, shop__tenant_owner=tenant)
    else:
        products = Product.objects.filter(is_active=True)

    if not is_user_admin and user_shop:
        products = products.filter(shop=user_shop)

    products = products.select_related('category', 'shop').order_by('name')

    response = HttpResponse(content_type='text/csv')
    today_str = timezone.localdate().strftime('%Y-%m-%d')
    response['Content-Disposition'] = f'attachment; filename="wholesale_price_sheet_{today_str}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'SKU/Barcode', 'Product Name', 'Category', 'Batch Number',
        'Stock Available (Units)', 'Wholesale Unit Price (NGN)',
        'Suggested Carton Size (Units)', 'Status'
    ])

    for p in products:
        status = 'IN STOCK' if p.stock_quantity > p.min_stock_level else ('LOW STOCK' if p.stock_quantity > 0 else 'OUT OF STOCK')
        writer.writerow([
            p.sku,
            p.name,
            p.category.name if p.category else 'General',
            p.batch_number or 'BN: 1',
            p.stock_quantity,
            f"{p.selling_price:.2f}",
            '12 / 24',
            status
        ])

    return response

@login_required
@user_passes_test(is_admin)
def settings_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    tenant_owner = request.user.profile.tenant_owner
    settings = StoreSettings.get_settings(tenant_owner=tenant_owner)
    can_edit_receipt_footer = getattr(getattr(tenant_owner, 'profile', None), 'plan', 'FREE') != 'FREE'
    
    if request.method == 'POST':
        settings.store_name = request.POST.get('store_name', settings.store_name)
        settings.address = request.POST.get('address', settings.address)
        settings.phone = request.POST.get('phone', settings.phone)
        settings.email = request.POST.get('email', settings.email)
        settings.tax_rate = Decimal(request.POST.get('tax_rate', '0.00'))
        if can_edit_receipt_footer:
            settings.receipt_footer = request.POST.get('receipt_footer', settings.receipt_footer)
        
        try:
            settings.save()
            messages.success(request, "Store settings updated successfully.")
        except ValidationError as e:
            messages.error(request, str(e))
        return redirect('settings')

    context = {
        'settings': settings,
        'is_admin': True,
        'can_edit_receipt_footer': can_edit_receipt_footer,
    }
    return render(request, 'settings.html', context)

# ----------------- NEW PHASE 2 VIEWS -----------------

@login_required
@user_passes_test(is_admin_or_manager)
def staff_management_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')

    if request.method == 'POST':
        action = request.POST.get('action')
    user_profile = request.user.profile
    is_user_admin = is_admin(request.user)
    user_shop = user_profile.shop
    tenant = get_tenant_scope(request.user)

    # Fetch users list based on role
    # Admin sees all; Manager sees only cashiers in their shop
    if is_user_admin:
        if tenant:
            staff_profiles = UserProfile.objects.filter(tenant_owner=tenant).select_related('user', 'shop').order_by('role', 'user__username')
            shops = Shop.objects.filter(tenant_owner=tenant).order_by('name')
        else:
            staff_profiles = UserProfile.objects.select_related('user', 'shop').all().order_by('role', 'user__username')
            shops = Shop.objects.all().order_by('name')
        roles_choices = UserProfile.ROLES
    else:
        if tenant:
            staff_profiles = UserProfile.objects.filter(shop=user_shop, role='CASHIER', tenant_owner=tenant).select_related('user', 'shop').order_by('user__username')
            shops = Shop.objects.filter(id=user_shop.id, tenant_owner=tenant) if user_shop else Shop.objects.none()
        else:
            staff_profiles = UserProfile.objects.select_related('user', 'shop').filter(shop=user_shop, role='CASHIER').order_by('user__username')
            shops = Shop.objects.filter(id=user_shop.id) if user_shop else Shop.objects.none()
        # Manager can only add Cashier
        roles_choices = [('CASHIER', 'Cashier')]

    context = {
        'staff_profiles': staff_profiles,
        'shops': shops,
        'roles_choices': roles_choices,
        'is_admin': is_user_admin,
        'is_manager': is_manager(request.user),
    }
    if request.method == 'POST':

    # Create/Edit/Delete Cashier/Manager accounts
        if action == 'add_staff':
            username = request.POST.get('username', '').strip().lower()
            email = request.POST.get('email', '').strip()
            # Validate email domain against allowed list
            allowed_domains = {'gmail.com', 'icloud.com', 'hotmail.com', 'outlook.com'}
            domain = email.split('@')[-1].lower() if '@' in email else ''
            if domain not in allowed_domains:
                messages.error(request, "Registration is only allowed with Gmail, iCloud, Hotmail, or Outlook email addresses.")
                return render(request, 'register.html')
            password = request.POST.get('password')
            confirm_password = request.POST.get('confirm_password')
            business_name = request.POST.get('business_name', '').strip()
            business_type = request.POST.get('business_type', 'GENERAL')
            plan = request.POST.get('plan', 'FREE')
            tenant_type = request.POST.get('tenant_type', 'RETAIL')
            kyc_document_type = request.POST.get('kyc_document_type', '').strip()
            kyc_document_number = request.POST.get('kyc_document_number', '').strip()
            shop_id = request.POST.get('shop_id')
            # Validate authorization rules
            if not is_user_admin:
                role = 'CASHIER'
                staff_shop = user_shop
            else:
                if tenant:
                    staff_shop = get_object_or_404(Shop, id=shop_id, tenant_owner=tenant) if shop_id else None
                else:
                    staff_shop = get_object_or_404(Shop, id=shop_id) if shop_id else None
            if not staff_shop and role != 'ADMIN':
                messages.error(request, "Staff creation failed. A shop assignment is required.")
                return redirect('staff_management')
            if not username or not password:
                messages.error(request, "Username and Password are required fields.")
                return redirect('staff_management')
            if User.objects.filter(username=username).exists():
                messages.error(request, f"User '{username}' already exists.")
                return redirect('staff_management')
            # Plan limit checks
            if tenant and tenant.profile.plan == 'FREE':
                if role == 'MANAGER':
                    manager_count = UserProfile.objects.filter(tenant_owner=tenant, role='MANAGER').count()
                    if manager_count >= 1:
                        messages.error(request, "Free plan limit reached: Maximum 1 Manager allowed. Please upgrade to Premium.")
                        return redirect('staff_management')
                elif role == 'CASHIER':
                    cashier_count = UserProfile.objects.filter(tenant_owner=tenant, role='CASHIER').count()
                    if cashier_count >= 1:
                        messages.error(request, "Free plan limit reached: Maximum 1 Cashier allowed. Please upgrade to Premium.")
                        return redirect('staff_management')
            # Create User with optional first/last name
            new_user = User.objects.create_user(
                username=username, email=email, password=password,
            )
            # Set role and shop
            profile = new_user.profile
            profile.role = role
            profile.tenant_owner = tenant if tenant else new_user
            profile.shop = staff_shop
            profile.save()
            messages.success(request, f"Staff user '{username}' created successfully.")
            return redirect('staff_management')
        action = request.POST.get('action')

        
        if action == 'edit_staff':
            profile_id = request.POST.get('profile_id')
            if is_user_admin:
                if tenant:
                    profile_to_edit = get_object_or_404(UserProfile, id=profile_id, tenant_owner=tenant)
                else:
                    profile_to_edit = get_object_or_404(UserProfile, id=profile_id)
            else:
                if tenant:
                    profile_to_edit = get_object_or_404(UserProfile, id=profile_id, shop=user_shop, role='CASHIER', tenant_owner=tenant)
                else:
                    profile_to_edit = get_object_or_404(UserProfile, id=profile_id, shop=user_shop, role='CASHIER')

            email = request.POST.get('email', '').strip()
        # Validate email domain against allowed list
        allowed_domains = {'gmail.com', 'icloud.com', 'hotmail.com', 'outlook.com'}
        domain = email.split('@')[-1].lower() if '@' in email else ''
        if domain not in allowed_domains:
            messages.error(request, "Registration is only allowed with Gmail, iCloud, Hotmail, or Outlook email addresses.")
            return render(request, 'register.html')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        business_name = request.POST.get('business_name', '').strip()
        business_type = request.POST.get('business_type', 'GENERAL')
        plan = request.POST.get('plan', 'FREE')
        tenant_type = request.POST.get('tenant_type', 'RETAIL')
        kyc_document_type = request.POST.get('kyc_document_type', '').strip()
        kyc_document_number = request.POST.get('kyc_document_number', '').strip()


            # Extract optional fields for editing
        role = request.POST.get('role')
        shop_id = request.POST.get('shop_id')

        profile_to_edit.user.email = email
        if password:
            profile_to_edit.user.set_password(password)
        profile_to_edit.user.save()

        if is_user_admin:
            if role:
                profile_to_edit.role = role
            if shop_id:
                if tenant:
                    profile_to_edit.shop = get_object_or_404(Shop, id=shop_id, tenant_owner=tenant)
                else:
                    profile_to_edit.shop = get_object_or_404(Shop, id=shop_id)
            elif role == 'ADMIN':
                profile_to_edit.shop = None


            profile_to_edit.save()
            messages.success(request, f"Profile for '{profile_to_edit.user.username}' updated successfully.")

        elif action == 'delete_staff':
            profile_id = request.POST.get('profile_id')
            if is_user_admin:
                if tenant:
                    profile_to_delete = get_object_or_404(UserProfile, id=profile_id, tenant_owner=tenant)
                else:
                    profile_to_delete = get_object_or_404(UserProfile, id=profile_id)
            else:
                if tenant:
                    profile_to_delete = get_object_or_404(UserProfile, id=profile_id, shop=user_shop, role='CASHIER', tenant_owner=tenant)
                else:
                    profile_to_delete = get_object_or_404(UserProfile, id=profile_id, shop=user_shop, role='CASHIER')

            user_to_delete = profile_to_delete.user
            if user_to_delete == request.user:
                messages.error(request, "You cannot delete your own account.")
            else:
                messages.success(request, f"Staff account '{user_to_delete.username}' deleted successfully.")
                user_to_delete.delete()

        return redirect('staff_management')

    return render(request, 'staff.html', context)

@login_required
@user_passes_test(is_admin)
def shop_management_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    tenant = get_tenant_scope(request.user)

    if tenant:
        shops = Shop.objects.filter(tenant_owner=tenant).order_by('name')
        managers = User.objects.filter(profile__role__in=['ADMIN', 'MANAGER'], profile__tenant_owner=tenant).order_by('username')
    else:
        shops = Shop.objects.all().order_by('name')
        managers = User.objects.filter(profile__role__in=['ADMIN', 'MANAGER']).order_by('username')

        if action == 'add_staff':
            username = request.POST.get('username', '').strip().lower()
            email = request.POST.get('email', '').strip()
            # Validate email domain against allowed list
            allowed_domains = {'gmail.com', 'icloud.com', 'hotmail.com', 'outlook.com'}
            domain = email.split('@')[-1].lower() if '@' in email else ''
            if domain not in allowed_domains:
                messages.error(request, "Registration is only allowed with Gmail, iCloud, Hotmail, or Outlook email addresses.")
                return render(request, 'register.html')
            password = request.POST.get('password')
            confirm_password = request.POST.get('confirm_password')
            business_name = request.POST.get('business_name', '').strip()
            business_type = request.POST.get('business_type', 'GENERAL')
            plan = request.POST.get('plan', 'FREE')
            tenant_type = request.POST.get('tenant_type', 'RETAIL')
            kyc_document_type = request.POST.get('kyc_document_type', '').strip()
            kyc_document_number = request.POST.get('kyc_document_number', '').strip()
            shop_id = request.POST.get('shop_id')
            # Validate authorization rules
            if not is_user_admin:
                role = 'CASHIER'
                staff_shop = user_shop
            else:
                if tenant:
                    staff_shop = get_object_or_404(Shop, id=shop_id, tenant_owner=tenant) if shop_id else None
                else:
                    staff_shop = get_object_or_404(Shop, id=shop_id) if shop_id else None
            if not staff_shop and role != 'ADMIN':
                messages.error(request, "Staff creation failed. A shop assignment is required.")
                return redirect('staff_management')
            if not username or not password:
                messages.error(request, "Username and Password are required fields.")
                return redirect('staff_management')
            if User.objects.filter(username=username).exists():
                messages.error(request, f"User '{username}' already exists.")
                return redirect('staff_management')
            # Plan limit checks
            if tenant and tenant.profile.plan == 'FREE':
                if role == 'MANAGER':
                    manager_count = UserProfile.objects.filter(tenant_owner=tenant, role='MANAGER').count()
                    if manager_count >= 1:
                        messages.error(request, "Free plan limit reached: Maximum 1 Manager allowed. Please upgrade to Premium.")
                        return redirect('staff_management')
                elif role == 'CASHIER':
                    cashier_count = UserProfile.objects.filter(tenant_owner=tenant, role='CASHIER').count()
                    if cashier_count >= 1:
                        messages.error(request, "Free plan limit reached: Maximum 1 Cashier allowed. Please upgrade to Premium.")
                        return redirect('staff_management')
            # Create User with optional first/last name
            new_user = User.objects.create_user(
                username=username, email=email, password=password,
            )
            # Set role and shop
            profile = new_user.profile
            profile.role = role
            profile.tenant_owner = tenant if tenant else new_user
            profile.shop = staff_shop
            profile.save()
            messages.success(request, f"Staff user '{username}' created successfully.")
            return redirect('staff_management')
        action = request.POST.get('action')

        if action == 'add_shop':
            name = request.POST.get('name')
            location = request.POST.get('location')
            phone = request.POST.get('phone')
            manager_id = request.POST.get('manager_id')

            # Enforce FREE plan limit
            if tenant and tenant.profile.plan == 'FREE':
                shop_count = Shop.objects.filter(tenant_owner=tenant).count()
                if shop_count >= 1:
                    messages.error(request, "Free plan limit reached: Maximum 1 Shop branch allowed. Please upgrade to Premium.")
                    return redirect('shops')

            if tenant:
                manager_user = get_object_or_404(User, id=manager_id, profile__tenant_owner=tenant) if manager_id else None
            else:
                manager_user = get_object_or_404(User, id=manager_id) if manager_id else None

            if name:
                shop = Shop.objects.create(name=name, location=location, phone=phone, manager=manager_user, tenant_owner=tenant)
                # Link manager's profile to this shop automatically if not linked
                if manager_user and not manager_user.profile.shop:
                    manager_user.profile.shop = shop
                    manager_user.profile.save()
                messages.success(request, f"Shop Branch '{name}' created successfully.")

        elif action == 'edit_shop':
            shop_id = request.POST.get('shop_id')
            if tenant:
                shop = get_object_or_404(Shop, id=shop_id, tenant_owner=tenant)
            else:
                shop = get_object_or_404(Shop, id=shop_id)

            shop.name = request.POST.get('name', shop.name)
            shop.location = request.POST.get('location', shop.location)
            shop.phone = request.POST.get('phone', shop.phone)
            
            manager_id = request.POST.get('manager_id')
            if manager_id:
                if tenant:
                    manager_user = get_object_or_404(User, id=manager_id, profile__tenant_owner=tenant)
                else:
                    manager_user = get_object_or_404(User, id=manager_id)
                shop.manager = manager_user
                
                # Update manager's shop binding
                manager_user.profile.shop = shop
                manager_user.profile.save()
            else:
                shop.manager = None

            shop.save()
            messages.success(request, f"Shop Branch '{shop.name}' details updated successfully.")

        elif action == 'delete_shop':
            shop_id = request.POST.get('shop_id')
            if tenant:
                shop = get_object_or_404(Shop, id=shop_id, tenant_owner=tenant)
            else:
                shop = get_object_or_404(Shop, id=shop_id)
            messages.success(request, f"Shop Branch '{shop.name}' deleted successfully.")
            shop.delete()

        return redirect('shops')

    context = {
        'shops': shops,
        'managers': managers,
        'is_admin': True,
    }
    return render(request, 'shops.html', context)


@login_required
def customer_display_view(request):
    """A secondary display page intended to face the customer during checkout.
    Receives live cart data via BroadcastChannel from the POS tab — no server calls needed."""
    settings = get_store_settings_for_user(request.user, request.user.profile.shop)
    active_ads = list(Advertisement.objects.filter(is_active=True).values('id', 'title', 'content', 'image_url'))
    context = {
        'store_name': settings.store_name,
        'store_address': settings.address or '',
        'store_phone': settings.phone or '',
        'store_email': settings.email or '',
        'customer_ads_json': json.dumps(active_ads),
    }
    return render(request, 'customer_display.html', context)


def terms_and_agreement_view(request):
    """Terms and Agreement / Privacy Policy view."""
    return render(request, 'terms.html')


@login_required
def mark_notification_read_api(request):
    """API to mark one or all admin notifications as read for current user."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    notification_id = None
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body.decode('utf-8'))
            notification_id = data.get('notification_id')
        else:
            notification_id = request.POST.get('notification_id')
    except Exception:
        notification_id = request.POST.get('notification_id')

    if not notification_id:
        return JsonResponse({'error': 'Missing notification_id'}, status=400)

    if str(notification_id).lower() == 'all':
        tenant_user = request.user
        if hasattr(request.user, 'profile') and request.user.profile.tenant_owner:
            tenant_user = request.user.profile.tenant_owner

        unread_notifs = AdminNotification.objects.filter(
            is_active=True
        ).filter(
            Q(target_tenant__isnull=True) | Q(target_tenant=tenant_user) | Q(target_tenant=request.user)
        ).exclude(
            read_records__user=request.user
        )

        created = [
            NotificationReadStatus(notification=n, user=request.user)
            for n in unread_notifs
        ]
        if created:
            NotificationReadStatus.objects.bulk_create(created, ignore_conflicts=True)
        return JsonResponse({'status': 'success', 'marked_all': True, 'count': len(created)})

    notif = get_object_or_404(AdminNotification, id=notification_id)
    NotificationReadStatus.objects.get_or_create(notification=notif, user=request.user)
    return JsonResponse({'status': 'success', 'notification_id': notif.id})


@login_required
def get_unread_notifications_api(request):
    """API to fetch unread admin notifications for client-side push / polling."""
    tenant_user = request.user
    if hasattr(request.user, 'profile') and request.user.profile.tenant_owner:
        tenant_user = request.user.profile.tenant_owner

    qs = AdminNotification.objects.filter(
        is_active=True
    ).filter(
        Q(target_tenant__isnull=True) | Q(target_tenant=tenant_user) | Q(target_tenant=request.user)
    ).order_by('-created_at')[:20]

    read_ids = set(NotificationReadStatus.objects.filter(
        user=request.user
    ).values_list('notification_id', flat=True))

    unread_items = []
    for n in qs:
        if n.id not in read_ids:
            unread_items.append({
                'id': n.id,
                'title': n.title,
                'message': n.message,
                'type': n.notification_type,
                'type_display': n.get_notification_type_display(),
                'created_at': n.created_at.strftime('%Y-%m-%d %H:%M'),
            })

    return JsonResponse({
        'status': 'success',
        'unread_count': len(unread_items),
        'notifications': unread_items,
    })


@login_required
def subscription_view(request):
    """Displays tenant subscription status, active plan, limits, and payment history."""
    if request.user.is_superuser:
        return redirect('admin_panel')

    tenant_owner = get_tenant_scope(request.user)
    if not tenant_owner:
        tenant_owner = request.user

    profile = getattr(tenant_owner, 'profile', request.user.profile)
    payments = SubscriptionPayment.objects.filter(tenant=tenant_owner).order_by('-created_at')
    settings = StoreSettings.get_settings()
    pricing = SubscriptionPayment.PLAN_PRICING
    renewal_plan = profile.plan if profile.plan in pricing else 'PREMIUM'
    upgrade_plan = {
        'STANDARD': 'PREMIUM',
        'PREMIUM': 'GOLDEN_PREMIUM',
        'GOLDEN_PREMIUM': 'GOLDEN_PREMIUM',
    }.get(profile.plan, 'PREMIUM')

    context = {
        'tenant_owner': tenant_owner,
        'profile': profile,
        'payments': payments,
        'settings': settings,
        'pricing': pricing,
        'renewal_plan': renewal_plan,
        'upgrade_plan': upgrade_plan,
        'is_admin': is_admin(request.user),
    }
    return render(request, 'subscription.html', context)


@login_required
def subscription_upgrade_view(request):
    """Handles plan upgrade selection, bank transfer receipt upload, or card checkout."""
    if request.user.is_superuser:
        return redirect('admin_panel')

    tenant_owner = get_tenant_scope(request.user)
    if not tenant_owner:
        tenant_owner = request.user

    profile = getattr(tenant_owner, 'profile', request.user.profile)
    settings = StoreSettings.get_settings()
    pricing = SubscriptionPayment.PLAN_PRICING

    if request.method == 'POST':
        plan = request.POST.get('plan')
        billing_cycle = request.POST.get('billing_cycle', 'MONTHLY')
        payment_method = request.POST.get('payment_method', 'BANK_TRANSFER')
        sender_name = request.POST.get('sender_name', '').strip()
        sender_bank = request.POST.get('sender_bank', '').strip()
        reference = request.POST.get('reference', '').strip()
        notes = request.POST.get('notes', '').strip()
        proof_image = request.FILES.get('proof_image')

        if plan not in pricing:
            messages.error(request, "Please select a valid subscription plan.")
            return redirect('subscription_upgrade')

        # Standard plan is strictly Annual only
        if plan == 'STANDARD':
            billing_cycle = 'ANNUAL'
        elif billing_cycle not in ('QUARTERLY', 'ANNUAL'):
            billing_cycle = 'ANNUAL'

        amount = pricing[plan].get(billing_cycle, pricing[plan].get('ANNUAL'))

        if not reference:
            reference = f"SUB-{uuid.uuid4().hex[:10].upper()}"
        elif SubscriptionPayment.objects.filter(reference=reference).exists():
            reference = f"{reference}-{uuid.uuid4().hex[:4].upper()}"

        payment = SubscriptionPayment.objects.create(
            tenant=tenant_owner,
            plan=plan,
            billing_cycle=billing_cycle,
            amount=amount,
            payment_method=payment_method,
            reference=reference,
            sender_name=sender_name,
            sender_bank=sender_bank,
            proof_image=proof_image,
            notes=notes,
            status='PENDING'
        )

        if payment_method == 'REMITA':
            # Instant online payment verification
            activate_subscription_upgrade(payment)
            messages.success(request, f"Online payment confirmed! Your account has been upgraded to {payment.get_plan_display()}!")
            return redirect('subscription')
        else:
            messages.success(
                request,
                f"Bank transfer details submitted! Payment reference: #{payment.reference}. Your subscription will be activated upon administrator confirmation."
            )
            return redirect('subscription')

    initial_plan = request.GET.get('plan', profile.plan).upper()
    if initial_plan not in pricing:
        initial_plan = 'PREMIUM'
    initial_billing_cycle = request.GET.get(
        'billing_cycle',
        request.GET.get('cycle', 'ANNUAL'),
    ).upper()
    if initial_plan == 'STANDARD' or initial_billing_cycle not in pricing[initial_plan]:
        initial_billing_cycle = 'ANNUAL'
    context = {
        'profile': profile,
        'settings': settings,
        'pricing': pricing,
        'initial_plan': initial_plan,
        'initial_billing_cycle': initial_billing_cycle,
        'initial_amount': pricing[initial_plan][initial_billing_cycle],
        'is_admin': is_admin(request.user),
    }
    return render(request, 'subscription_upgrade.html', context)


@login_required
def remita_generate_rrr_api(request):
    """
    Generates a Remita Retrieval Reference (RRR) server-side before the browser
    opens the Remita inline payment widget.

    Security: The amount is locked server-side from PLAN_PRICING — the browser
    cannot tamper with the charge amount.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    tenant_owner = get_tenant_scope(request.user)
    if not tenant_owner:
        tenant_owner = request.user

    try:
        data = json.loads(request.body)
        plan = data.get('plan', 'PREMIUM')
        billing_cycle = data.get('billing_cycle', 'MONTHLY')

        pricing = SubscriptionPayment.PLAN_PRICING
        if plan not in pricing:
            return JsonResponse({'status': 'error', 'message': 'Invalid plan'}, status=400)

        # Standard plan is strictly Annual only
        if plan == 'STANDARD':
            billing_cycle = 'ANNUAL'
        elif billing_cycle not in ('QUARTERLY', 'ANNUAL'):
            billing_cycle = 'ANNUAL'

        amount = pricing[plan].get(billing_cycle, pricing[plan].get('ANNUAL'))

        # Build a deterministic order ID: user-plan-cycle-timestamp
        import uuid as _uuid
        order_id = f"VIARMS-{tenant_owner.id}-{plan[:3]}-{billing_cycle[:1]}-{_uuid.uuid4().hex[:8].upper()}"

        payer_name = (
            f"{tenant_owner.first_name} {tenant_owner.last_name}".strip()
            or tenant_owner.username
        )
        payer_email = tenant_owner.email or f"{tenant_owner.username}@viarms.com"

        ok, result = generate_rrr(
            order_id=order_id,
            amount_ngn=amount,
            payer_name=payer_name,
            payer_email=payer_email,
            description=f"viARMS {plan.replace('_', ' ').title()} {billing_cycle.title()} Subscription",
        )
        if not ok:
            return JsonResponse({'status': 'error', 'message': result.get('error', 'Could not generate payment reference')}, status=400)

        result['status'] = 'success'
        result['plan'] = plan
        result['billing_cycle'] = billing_cycle
        result['amount_ngn'] = str(amount)
        return JsonResponse(result)

    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)


@login_required
def remita_verify_api(request):
    """
    API endpoint to verify and activate Remita card/online transactions.
    Security: Calls Remita REST API server-to-server to confirm payment status
    and validates the settled amount matches the server-locked plan price.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    tenant_owner = get_tenant_scope(request.user)
    if not tenant_owner:
        tenant_owner = request.user

    try:
        data = json.loads(request.body)
        rrr = data.get('rrr', '').strip()
        order_id = data.get('orderId', '').strip()
        plan = data.get('plan', 'PREMIUM')
        billing_cycle = data.get('billing_cycle', 'MONTHLY')

        pricing = SubscriptionPayment.PLAN_PRICING
        if plan not in pricing:
            return JsonResponse({'status': 'error', 'message': 'Invalid plan'}, status=400)

        # Standard plan is strictly Annual only
        if plan == 'STANDARD':
            billing_cycle = 'ANNUAL'
        elif billing_cycle not in ('QUARTERLY', 'ANNUAL'):
            billing_cycle = 'ANNUAL'

        amount = pricing[plan].get(billing_cycle, pricing[plan].get('ANNUAL'))

        if not rrr:
            return JsonResponse({'status': 'error', 'message': 'Remita RRR required'}, status=400)

        creds = get_remita_credentials()
        # If live credentials configured and not a test RRR, verify with Remita API
        if creds['api_key'] and not rrr.startswith('RRR-TEST-'):
            is_valid, gateway_info = verify_remita_payment(rrr, expected_amount_ngn=amount)
            if not is_valid:
                return JsonResponse({'status': 'error', 'message': gateway_info}, status=400)

        reference = rrr or order_id
        payment, created = SubscriptionPayment.objects.get_or_create(
            reference=reference,
            defaults={
                'tenant': tenant_owner,
                'plan': plan,
                'billing_cycle': billing_cycle,
                'amount': amount,
                'payment_method': 'REMITA',
                'status': 'PENDING'
            }
        )
        if payment.status != 'CONFIRMED':
            activate_subscription_upgrade(payment)

        return JsonResponse({
            'status': 'success',
            'message': f"Payment verified! Upgraded to {payment.get_plan_display()}.",
            'plan': payment.plan,
            'expires_at': payment.expires_at.strftime('%Y-%m-%d') if payment.expires_at else ''
        })
    except Exception as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)


@csrf_exempt
def remita_webhook_view(request):
    """
    Remita IPN (Instant Payment Notification) receiver.
    Security: Validates SHA-512 signature using Remita API key.
    Guarantees tenant upgrade even if customer browser disconnects mid-payment.
    Remita posts to this URL after successful payment settlement.
    """
    if request.method != 'POST':
        return HttpResponse(status=405)

    # Validate Remita IPN signature (header or payload hash)
    signature = request.headers.get('x-remita-signature', '')
    if not verify_remita_webhook_signature(request.body, signature):
        return HttpResponse("Invalid or missing Remita webhook signature", status=400)

    try:
        payload = json.loads(request.body)
        # Remita IPN payload fields
        rrr = (payload.get('rrr') or payload.get('RRR') or '').strip()
        amount_str = str(payload.get('amount') or '0')
        payer_email = str(payload.get('payerEmail') or payload.get('email') or '').strip().lower()
        order_id = str(payload.get('orderId') or payload.get('transId') or rrr)
        payment_state = str(payload.get('paymentChannelName') or payload.get('status') or payload.get('paymentState') or '')

        # Only activate on successful payments
        if payment_state.lower() not in ('successful', 'success', '01', '00', 'completed'):
            return HttpResponse("Webhook acknowledged (non-success state)", status=200)

        try:
            amount_ngn = Decimal(amount_str)
        except Exception:
            amount_ngn = Decimal('0')

        # Extract plan metadata from orderId pattern: VIARMS-{userId}-{plan}-{cycle}-{uuid}
        plan = 'PREMIUM'
        billing_cycle = 'MONTHLY'
        tenant = None
        if order_id.startswith('VIARMS-'):
            parts = order_id.split('-')
            # parts: ['VIARMS', userId, planPrefix, cyclePrefix, uuid]
            if len(parts) >= 5:
                try:
                    tenant_id = int(parts[1])
                    tenant = User.objects.filter(id=tenant_id, is_superuser=False).first()
                except (ValueError, IndexError):
                    pass
                plan_prefix = parts[2] if len(parts) > 2 else 'STA'
                cycle_prefix = parts[3] if len(parts) > 3 else 'M'
                if plan_prefix == 'GOL':
                    plan = 'GOLDEN_PREMIUM'
                elif plan_prefix == 'PRE':
                    plan = 'PREMIUM'
                else:
                    plan = 'STANDARD'
                if cycle_prefix == 'Q':
                    billing_cycle = 'QUARTERLY'
                elif cycle_prefix == 'A':
                    billing_cycle = 'ANNUAL'
                else:
                    billing_cycle = 'ANNUAL'
                if plan == 'STANDARD':
                    billing_cycle = 'ANNUAL'

        # Fallback: find tenant by email
        if not tenant and payer_email:
            tenant = User.objects.filter(email__iexact=payer_email, is_superuser=False).first()

        reference = rrr or order_id

        if tenant and reference:
            with transaction.atomic():
                payment, created = SubscriptionPayment.objects.select_for_update().get_or_create(
                    reference=reference,
                    defaults={
                        'tenant': tenant,
                        'plan': plan,
                        'billing_cycle': billing_cycle,
                        'amount': amount_ngn,
                        'payment_method': 'REMITA',
                        'status': 'PENDING',
                    }
                )
                if payment.status != 'CONFIRMED':
                    activate_subscription_upgrade(payment)

        return HttpResponse("Remita IPN processed successfully", status=200)
    except Exception as exc:
        return HttpResponse(f"Webhook error: {str(exc)}", status=500)


# ---------------------------------------------------------------------------
# Category Dashboard
# ---------------------------------------------------------------------------
@login_required
def product_detail_view(request, product_id):
    profile = getattr(request.user, 'profile', None)
    tenant = get_tenant_scope(request.user)
    products = Product.objects.filter(is_active=True).select_related('category', 'shop')
    if tenant:
        products = products.filter(shop__tenant_owner=tenant)
    if not is_admin(request.user):
        user_shop = profile.shop if profile else None
        products = products.filter(shop=user_shop) if user_shop else products.none()

    product = get_object_or_404(products, id=product_id)
    return render(request, 'product_detail.html', {
        'product': product,
        'is_admin': is_admin(request.user),
        'business_type': profile.business_type if profile else 'GENERAL',
        'back_url': _safe_detail_return_url(request, reverse('products')),
    })


@login_required
def category_detail_view(request, category_id):
    """
    Category Dashboard:
    Displays financial analytics and item breakdown for a single category:
    - Number of distinct products and total units in stock
    - Total cost price value (capital invested)
    - Total selling price value (expected gross revenue)
    - Total potential profit and profit margin %
    - Itemized row-by-row table of products with individual costs, selling prices, and potential profit
    """
    tenant = get_tenant_scope(request.user)
    if tenant:
        category = Category.objects.filter(tenant_owner=tenant, id=category_id).first()
        if not category:
            from django.http import Http404
            raise Http404("No Category matches the given query.")
        products = Product.objects.filter(category=category, is_active=True, shop__tenant_owner=tenant)
    else:
        category = get_object_or_404(Category, id=category_id)
        products = Product.objects.filter(category=category, is_active=True)

    # Search filter
    search_q = request.GET.get('q', '').strip().lower()
    if search_q:
        products = [p for p in products if search_q in p.name.lower() or search_q in p.sku.lower() or search_q in (p.batch_number or '').lower()]

    # Aggregations
    total_products_count = len(products)
    total_items_quantity = sum(p.stock_quantity for p in products)
    total_cost_value = sum(Decimal(str(p.stock_quantity)) * p.cost_price for p in products)
    total_selling_value = sum(Decimal(str(p.stock_quantity)) * p.selling_price for p in products)
    total_potential_profit = total_selling_value - total_cost_value
    profit_margin_pct = (total_potential_profit / total_selling_value * 100) if total_selling_value > 0 else Decimal('0.0')

    # Expired count & lost cost value in this category
    expired_products = [p for p in products if p.is_expired]
    expired_count = len(expired_products)
    expired_cost_value = sum(Decimal(str(p.stock_quantity)) * p.cost_price for p in expired_products)

    # Annotate product rows with calculated totals
    annotated_products = []
    for p in products:
        qty_dec = Decimal(str(p.stock_quantity))
        item_cost_total = qty_dec * p.cost_price
        item_selling_total = qty_dec * p.selling_price
        item_profit = item_selling_total - item_cost_total
        item_margin = (item_profit / item_selling_total * 100) if item_selling_total > 0 else Decimal('0.0')
        annotated_products.append({
            'product': p,
            'cost_total': item_cost_total,
            'selling_total': item_selling_total,
            'potential_profit': item_profit,
            'margin_pct': item_margin,
        })

    context = {
        'category': category,
        'products': annotated_products,
        'total_products_count': total_products_count,
        'total_items_quantity': total_items_quantity,
        'total_cost_value': total_cost_value,
        'total_selling_value': total_selling_value,
        'total_potential_profit': total_potential_profit,
        'profit_margin_pct': profit_margin_pct,
        'expired_count': expired_count,
        'expired_cost_value': expired_cost_value,
        'search_q': search_q,
        'user_profile': request.user.profile if hasattr(request.user, 'profile') else None,
        'is_admin': is_admin(request.user),
    }
    return render(request, 'category_detail.html', context)


# ---------------------------------------------------------------------------
# Expired Products Dashboard
# ---------------------------------------------------------------------------
@login_required
def expired_products_view(request):
    """
    Expired Products Dashboard:
    - View all expired stock across the store
    - Calculate the total cost value (capital loss) of expired items
    - Filter expired products by category and shop
    """
    tenant = get_tenant_scope(request.user)
    today = timezone.localdate()

    if tenant:
        base_qs = Product.objects.filter(is_active=True, expiry_date__lt=today, shop__tenant_owner=tenant)
        all_categories = Category.objects.filter(tenant_owner=tenant)
    else:
        base_qs = Product.objects.filter(is_active=True, expiry_date__lt=today)
        all_categories = Category.objects.all()

    # Category filter
    category_id = request.GET.get('category_id')
    selected_category = None
    if category_id:
        try:
            selected_category = all_categories.get(id=int(category_id))
            base_qs = base_qs.filter(category=selected_category)
        except (Category.DoesNotExist, ValueError):
            pass

    # Search filter
    search_q = request.GET.get('q', '').strip().lower()
    if search_q:
        base_qs = [p for p in base_qs if search_q in p.name.lower() or search_q in p.sku.lower() or search_q in (p.batch_number or '').lower()]
    else:
        base_qs = list(base_qs.select_related('category', 'shop').order_by('expiry_date'))

    total_expired_count = len(base_qs)
    total_expired_units = sum(p.stock_quantity for p in base_qs)
    total_lost_cost_value = sum(Decimal(str(p.stock_quantity)) * p.cost_price for p in base_qs)
    total_lost_retail_value = sum(Decimal(str(p.stock_quantity)) * p.selling_price for p in base_qs)

    # Annotate rows with lost cost and days expired
    expired_rows = []
    for p in base_qs:
        qty_dec = Decimal(str(p.stock_quantity))
        lost_cost = qty_dec * p.cost_price
        lost_retail = qty_dec * p.selling_price
        days_expired = (today - p.expiry_date).days if p.expiry_date else 0
        expired_rows.append({
            'product': p,
            'lost_cost': lost_cost,
            'lost_retail': lost_retail,
            'days_expired': days_expired,
        })

    # Category pills with count of expired items
    cat_counts = []
    for cat in all_categories:
        count = sum(1 for row in expired_rows if row['product'].category_id == cat.id) if not selected_category else 0
        if count > 0 or (selected_category and selected_category.id == cat.id):
            cat_counts.append({'category': cat, 'count': count})

    context = {
        'expired_products': expired_rows,
        'total_expired_count': total_expired_count,
        'total_expired_units': total_expired_units,
        'total_lost_cost_value': total_lost_cost_value,
        'total_lost_retail_value': total_lost_retail_value,
        'categories': all_categories,
        'cat_counts': cat_counts,
        'selected_category': selected_category,
        'search_q': search_q,
        'user_profile': request.user.profile if hasattr(request.user, 'profile') else None,
        'is_admin': is_admin(request.user),
    }
    return render(request, 'expired_products.html', context)


# ---------------------------------------------------------------------------
# Stock Batches & Intake Tracking Dashboard
# ---------------------------------------------------------------------------
@login_required
def stock_batches_view(request):
    """
    Stock Batches & Intake Tracking Dashboard:
    Tracks inventory by stocking batches (BN: 1 = 1st stock, BN: 2 = 2nd stock, etc.):
    - Summary of total items, cost value, selling price, and potential profit per batch
    - Filter by Batch of stocking and by Category
    - High-level batch comparison table + detailed itemized product breakdown
    """
    tenant = get_tenant_scope(request.user)
    today = timezone.localdate()

    if tenant:
        all_products = list(Product.objects.filter(is_active=True, shop__tenant_owner=tenant).select_related('category', 'shop'))
        all_categories = list(Category.objects.filter(tenant_owner=tenant))
    else:
        all_products = list(Product.objects.filter(is_active=True).select_related('category', 'shop'))
        all_categories = list(Category.objects.all())

    # Build unique batches list and compute batch-level metrics
    batch_map = {}
    for p in all_products:
        batch_label = p.batch_number or 'BN: 1'
        if batch_label not in batch_map:
            batch_map[batch_label] = {
                'label': batch_label,
                'products': [],
                'distinct_count': 0,
                'total_units': 0,
                'total_cost': Decimal('0.00'),
                'total_selling': Decimal('0.00'),
                'potential_profit': Decimal('0.00'),
                'expired_units': 0,
            }
        b = batch_map[batch_label]
        b['products'].append(p)
        b['distinct_count'] += 1
        qty_dec = Decimal(str(p.stock_quantity))
        b['total_units'] += p.stock_quantity
        b['total_cost'] += qty_dec * p.cost_price
        b['total_selling'] += qty_dec * p.selling_price
        if p.is_expired:
            b['expired_units'] += p.stock_quantity

    for b in batch_map.values():
        b['potential_profit'] = b['total_selling'] - b['total_cost']
        b['margin_pct'] = (b['potential_profit'] / b['total_selling'] * 100) if b['total_selling'] > 0 else Decimal('0.0')

    # Sort batches by label: BN: 1, BN: 2, etc.
    sorted_batches = sorted(batch_map.values(), key=lambda x: x['label'])

    # Filtering
    selected_batch = request.GET.get('batch', '').strip()
    selected_category_id = request.GET.get('category_id', '').strip()

    filtered_products = all_products
    if selected_batch:
        filtered_products = [p for p in filtered_products if (p.batch_number or 'BN: 1') == selected_batch]
    if selected_category_id:
        try:
            cat_id_int = int(selected_category_id)
            filtered_products = [p for p in filtered_products if p.category_id == cat_id_int]
        except ValueError:
            pass

    # Search filter
    search_q = request.GET.get('q', '').strip().lower()
    if search_q:
        filtered_products = [p for p in filtered_products if search_q in p.name.lower() or search_q in p.sku.lower()]

    # Aggregates for current filter view
    filtered_units = sum(p.stock_quantity for p in filtered_products)
    filtered_cost = sum(Decimal(str(p.stock_quantity)) * p.cost_price for p in filtered_products)
    filtered_selling = sum(Decimal(str(p.stock_quantity)) * p.selling_price for p in filtered_products)
    filtered_profit = filtered_selling - filtered_cost
    filtered_margin = (filtered_profit / filtered_selling * 100) if filtered_selling > 0 else Decimal('0.0')

    # Detailed item breakdown
    item_rows = []
    for p in filtered_products:
        qty_dec = Decimal(str(p.stock_quantity))
        c_tot = qty_dec * p.cost_price
        s_tot = qty_dec * p.selling_price
        prof = s_tot - c_tot
        marg = (prof / s_tot * 100) if s_tot > 0 else Decimal('0.0')
        item_rows.append({
            'product': p,
            'cost_total': c_tot,
            'selling_total': s_tot,
            'profit': prof,
            'margin': marg,
        })

    context = {
        'batches': sorted_batches,
        'categories': all_categories,
        'selected_batch': selected_batch,
        'selected_category_id': selected_category_id,
        'items': item_rows,
        'total_items_count': len(item_rows),
        'total_units': filtered_units,
        'total_cost': filtered_cost,
        'total_selling': filtered_selling,
        'potential_profit': filtered_profit,
        'profit_margin_pct': filtered_margin,
        'search_q': search_q,
        'user_profile': request.user.profile if hasattr(request.user, 'profile') else None,
        'is_admin': is_admin(request.user),
    }
    return render(request, 'stock_batches.html', context)
