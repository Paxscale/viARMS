import csv
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
from django.contrib import messages
from django.core.files.base import ContentFile
from django.http import JsonResponse, HttpResponse
from django.db import IntegrityError, transaction
from django.db.models import Sum, F, Count, Q
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.models import User
from .models import Category, Product, Sale, SaleItem, StoreSettings, Shop, UserProfile, Advertisement

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
        sku = values['sku']
        if not name:
            row_errors.append("product name is required")
        elif len(name) > 200:
            row_errors.append("product name must be 200 characters or fewer")
        if not sku:
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
        if values['expiry_date'] and not (
            user_profile.business_type == 'UTENSILS' and material == 'PLASTIC'
        ):
            try:
                expiry = date.fromisoformat(values['expiry_date'])
            except ValueError:
                row_errors.append("expiry date must be a valid date")

        if sku and name and not row_errors:
            duplicate_key = (sku, product_shop.pk, expiry)
            if duplicate_key in submitted_keys:
                row_errors.append("another row in this submission has the same SKU and expiry date for this shop")
            elif Product.objects.filter(sku=sku, shop=product_shop, expiry_date=expiry).exists():
                row_errors.append("this SKU and expiry date already exist for this shop")
            submitted_keys.add(duplicate_key)

        if uploaded_image:
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
    try:
        with transaction.atomic():
            for product in products_to_create:
                product.save()
                if product.image:
                    saved_images.append((product.image.storage, product.image.name))
    except IntegrityError:
        for storage, image_name in saved_images:
            storage.delete(image_name)
        messages.error(request, "No products were added because a product conflicts with existing inventory.")
        return

    messages.success(request, f"Successfully added {len(products_to_create)} products to {product_shop.name}.")


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

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip().lower()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        business_name = request.POST.get('business_name', '').strip()
        business_type = request.POST.get('business_type', 'GENERAL')
        plan = request.POST.get('plan', 'FREE')
        kyc_document_type = request.POST.get('kyc_document_type', '').strip()
        kyc_document_number = request.POST.get('kyc_document_number', '').strip()

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, 'register.html')

        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' already exists.")
            return render(request, 'register.html')

        with transaction.atomic():
            user = User.objects.create_user(username=username, email=email, password=password)
            profile, created = UserProfile.objects.get_or_create(user=user)
            profile.role = 'ADMIN'
            profile.tenant_owner = user
            profile.plan = plan
            profile.business_type = business_type
            profile.is_approved = False
            profile.kyc_document_type = kyc_document_type
            profile.kyc_document_number = kyc_document_number
            profile.kyc_status = 'PENDING'
            profile.save()

            # Create first Shop branch automatically
            shop = Shop.objects.create(
                name=business_name,
                tenant_owner=user,
                manager=user
            )
            profile.shop = shop
            profile.save()

        messages.success(request, "Your account has been created successfully! It is pending administrator approval.")
        return redirect('login')

    return render(request, 'register.html')

def login_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin_panel')
        return redirect('dashboard')
    
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                # Check approval status
                profile, created = UserProfile.objects.get_or_create(user=user, defaults={'role': 'ADMIN' if user.is_superuser else 'CASHIER'})
                owner = profile.tenant_owner if profile.tenant_owner else user
                if not owner.is_active:
                    messages.error(request, "This business account has been suspended. Contact support for assistance.")
                    return redirect('login')
                if not owner.is_superuser and not owner.profile.is_approved:
                    messages.error(request, "Your account is pending administrator approval.")
                    return redirect('login')

                login(request, user)
                if user.is_superuser:
                    return redirect('admin_panel')
                return redirect('dashboard')
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
    return render(request, 'login.html', {'form': form})

def logout_view(request):
    logout(request)
    return redirect('login')

def landing_view(request):
    if request.user.is_authenticated:
        if request.user.is_superuser:
            return redirect('admin_panel')
        return redirect('dashboard')
    return render(request, 'landing.html')

def get_managed_tenant_profile(user_id):
    return get_object_or_404(
        UserProfile.objects.select_related('user', 'shop'),
        user_id=user_id,
        tenant_owner=F('user'),
        user__is_superuser=False,
    )

def delete_tenant_accounts(owner):
    User.objects.filter(profile__tenant_owner=owner).exclude(pk=owner.pk).delete()
    owner.delete()

@login_required
@user_passes_test(lambda u: u.is_superuser)
def admin_panel_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve_user':
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            if user_profile.kyc_status != 'VERIFIED':
                messages.error(request, f"Verify KYC for {user_profile.user.username} before approving the account.")
            else:
                user_profile.is_approved = True
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
                    delete_tenant_accounts(user)
                messages.success(request, f"Registration for {username} and its tenant data was permanently deleted.")
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
                        delete_tenant_accounts(user)
                    messages.success(request, f"Tenant {username} and its business data were permanently deleted.")
        elif action == 'update_plan':
            new_plan = request.POST.get('plan')
            user_profile = get_managed_tenant_profile(request.POST.get('user_id'))
            valid_plans = dict(UserProfile._meta.get_field('plan').choices)
            if new_plan not in valid_plans:
                messages.error(request, "Select a valid subscription plan.")
            else:
                user_profile.plan = new_plan
                user_profile.save(update_fields=['plan'])
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
        else:
            messages.error(request, "Unknown admin action.")

        redirect_url = reverse('admin_panel')
        tenant_search = request.POST.get('tenant_search', '').strip()
        if tenant_search:
            redirect_url = f"{redirect_url}?{urlencode({'tenant_search': tenant_search})}"
        return redirect(redirect_url)

    pending_users = UserProfile.objects.filter(
        is_approved=False,
        tenant_owner=F('user'),
        user__is_superuser=False,
    ).select_related('user', 'shop')
    pending_kyc = pending_users.exclude(kyc_status='VERIFIED')
    tenant_profiles = UserProfile.objects.filter(
        tenant_owner=F('user'),
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
        tenant_owner=F('user'),
        user__is_superuser=False,
    )
    ads = Advertisement.objects.all()

    context = {
        'pending_users': pending_users,
        'pending_kyc': pending_kyc,
        'all_tenants': all_tenants,
        'tenant_search': tenant_search,
        'active_tenant_count': all_tenant_profiles.filter(is_approved=True, user__is_active=True).count(),
        'suspended_tenant_count': all_tenant_profiles.filter(user__is_active=False).count(),
        'pending_tenant_count': all_tenant_profiles.filter(is_approved=False, user__is_active=True).count(),
        'ads': ads
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
def dashboard_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    user_profile = request.user.profile
    user_role = user_profile.role
    user_shop = user_profile.shop

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

    context = {
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

    settings = StoreSettings.get_settings()
    
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

    context = {
        'products': products,
        'categories': categories,
        'settings': settings,
        'products_json': json.dumps(products_json),
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
        settings = StoreSettings.get_settings()
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

        # Build receipt response details
        receipt_items = []
        for item in sale.items.all():
            name_str = item.product.name if item.product else 'Deleted Product'
            if item.product and item.product.strength:
                name_str = f"{name_str} ({item.product.strength})"
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
            'store_name': user_shop.name,
            'store_address': user_shop.location or settings.address,
            'store_phone': user_shop.phone or settings.phone,
            'receipt_footer': settings.receipt_footer,
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
                if tenant:
                    cat = get_object_or_404(Category, id=cat_id, tenant_owner=tenant)
                else:
                    cat = get_object_or_404(Category, id=cat_id)
                cat.name = request.POST.get('name')
                cat.description = request.POST.get('description')
                cat.save()
                messages.success(request, f"Category '{cat.name}' updated successfully.")
            
        elif action == 'delete_category':
            if not is_user_admin:
                messages.error(request, "Only Admins can manage categories.")
            else:
                cat_id = request.POST.get('category_id')
                if tenant:
                    cat = get_object_or_404(Category, id=cat_id, tenant_owner=tenant)
                else:
                    cat = get_object_or_404(Category, id=cat_id)
                cat.delete()
                messages.success(request, "Category deleted successfully.")

        # Products (Admin & Manager)
        elif action == 'add_product':
            name = request.POST.get('name')
            sku = request.POST.get('sku')
            cat_id = request.POST.get('category_id')
            cost = Decimal(request.POST.get('cost_price', 0.00))
            sell = Decimal(request.POST.get('selling_price', 0.00))
            qty = int(request.POST.get('stock_quantity', 0))
            min_lvl = int(request.POST.get('min_stock_level', 5))
            desc = request.POST.get('description')
            expiry = request.POST.get('expiry_date') or None

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

            if uploaded_image:
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

            if tenant:
                category = get_object_or_404(Category, id=cat_id, tenant_owner=tenant) if cat_id else None
            else:
                category = Category.objects.get(id=cat_id) if cat_id else None

            if name and sku:
                # New rule: same SKU allowed in same shop if expiry date differs
                duplicate = Product.objects.filter(
                    sku=sku, shop=prod_shop, expiry_date=expiry, is_active=True
                ).exists()
                if duplicate:
                    messages.error(request, f"A product with SKU '{sku}' and the same expiry date already exists in this shop. Use a different expiry date or adjust the existing stock.")
                else:
                    Product.objects.create(
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
                    messages.success(request, f"Product '{name}' created successfully under '{prod_shop.name}'.")

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
            sku = request.POST.get('sku')
            new_expiry = request.POST.get('expiry_date') or None
            new_material = request.POST.get('material', '')
            uploaded_image = request.FILES.get('image')

            if uploaded_image:
                try:
                    uploaded_image = prepare_product_image(uploaded_image)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect('products')

            if user_profile.business_type == 'UTENSILS' and new_material == 'PLASTIC':
                new_expiry = None
            target_shop = prod.shop
            # Check uniqueness: same sku + shop + expiry but a DIFFERENT product
            conflict = Product.objects.filter(
                sku=sku, shop=target_shop, expiry_date=new_expiry, is_active=True
            ).exclude(id=prod.id).exists()
            if conflict:
                messages.error(request, f"Another product with SKU '{sku}' and the same expiry date already exists in this shop.")
            else:
                prod.sku = sku
                if cat_id := request.POST.get('category_id'):
                    if tenant:
                        prod.category = get_object_or_404(Category, id=cat_id, tenant_owner=tenant)
                    else:
                        prod.category = Category.objects.get(id=cat_id)
                else:
                    prod.category = None
                prod.cost_price = Decimal(request.POST.get('cost_price', 0.00))
                prod.selling_price = Decimal(request.POST.get('selling_price', 0.00))
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
                prod.batch_number = request.POST.get('batch_number', '')
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
                if old_image_name and old_image_name != (prod.image.name if prod.image else None):
                    prod.image.storage.delete(old_image_name)
                messages.success(request, f"Product '{prod.name}' updated successfully.")

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

    context = {
        'sales': sales,
        'cashiers': cashiers,
        'shops': shops,
        'payment_choices': payment_choices,
        'is_admin': is_user_admin,
        'is_manager': is_manager(request.user),
    }
    return render(request, 'sales_history.html', context)

@login_required
def sale_detail_json(request, sale_id):
    user_profile = request.user.profile
    is_user_admin = is_admin(request.user)
    user_shop = user_profile.shop
    tenant = get_tenant_scope(request.user)

    if is_user_admin:
        if tenant:
            sale = get_object_or_404(Sale, id=sale_id, shop__tenant_owner=tenant)
        else:
            sale = get_object_or_404(Sale, id=sale_id)
    else:
        if tenant:
            sale = get_object_or_404(Sale, id=sale_id, shop=user_shop, shop__tenant_owner=tenant)
        else:
            sale = get_object_or_404(Sale, id=sale_id, shop=user_shop)

    items_data = []
    for item in sale.items.all():
        items_data.append({
            'name': item.product.name if item.product else 'Deleted Product',
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
        'store_name': sale.shop.name if sale.shop else "viARMS Hub",
        'store_address': sale.shop.location if sale.shop and sale.shop.location else "Lagos, Nigeria",
        'store_phone': sale.shop.phone if sale.shop and sale.shop.phone else "",
        'receipt_footer': "Thank you for your business!",
        'items': items_data
    }
    return JsonResponse({'success': True, 'receipt': receipt_data})

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
@user_passes_test(is_admin)
def settings_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
    settings = StoreSettings.get_settings()
    
    if request.method == 'POST':
        settings.store_name = request.POST.get('store_name', settings.store_name)
        settings.address = request.POST.get('address', settings.address)
        settings.phone = request.POST.get('phone', settings.phone)
        settings.tax_rate = Decimal(request.POST.get('tax_rate', '0.00'))
        settings.receipt_footer = request.POST.get('receipt_footer', settings.receipt_footer)
        
        try:
            settings.save()
            messages.success(request, "Global store settings updated successfully.")
        except ValidationError as e:
            messages.error(request, str(e))
        return redirect('settings')

    context = {
        'settings': settings,
        'is_admin': True,
    }
    return render(request, 'settings.html', context)

# ----------------- NEW PHASE 2 VIEWS -----------------

@login_required
@user_passes_test(is_admin_or_manager)
def staff_management_view(request):
    if request.user.is_superuser:
        return redirect('admin_panel')
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

    # Create/Edit/Delete Cashier/Manager accounts
    if request.method == 'POST' and 'action' in request.POST:
        action = request.POST.get('action')

        if action == 'add_staff':
            username = request.POST.get('username', '').strip().lower()
            email = request.POST.get('email', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            password = request.POST.get('password')
            role = request.POST.get('role', 'CASHIER')
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
                first_name=first_name, last_name=last_name
            )
            # Create/Update profile
            profile = new_user.profile
            profile.role = role
            profile.shop = staff_shop
            profile.tenant_owner = tenant
            profile.is_approved = True # Staff accounts are auto-approved under owner
            profile.save()

            messages.success(request, f"Account for '{username}' ({profile.get_role_display()}) onboarded successfully.")

        elif action == 'edit_staff':
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
            password = request.POST.get('password')
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

    context = {
        'staff_profiles': staff_profiles,
        'shops': shops,
        'roles_choices': roles_choices,
        'is_admin': is_user_admin,
        'is_manager': is_manager(request.user),
    }
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

    if request.method == 'POST' and 'action' in request.POST:
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
    settings = StoreSettings.get_settings()
    active_ads = list(Advertisement.objects.filter(is_active=True).values('id', 'title', 'content', 'image_url'))
    context = {
        'store_name': settings.store_name,
        'store_address': settings.address or '',
        'customer_ads_json': json.dumps(active_ads),
    }
    return render(request, 'customer_display.html', context)
