import csv
import json
from datetime import datetime, timedelta
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db import transaction
from django.db.models import Sum, F, Count
from django.utils import timezone
from django.contrib.auth.models import User
from .models import Category, Product, Sale, SaleItem, StoreSettings, Shop, UserProfile

def is_admin(user):
    return user.is_superuser or (hasattr(user, 'profile') and user.profile.role == 'ADMIN')

def is_manager(user):
    return hasattr(user, 'profile') and user.profile.role == 'MANAGER'

def is_admin_or_manager(user):
    return is_admin(user) or is_manager(user)

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                # Ensure profile exists
                UserProfile.objects.get_or_create(user=user, defaults={'role': 'ADMIN' if user.is_superuser else 'CASHIER'})
                return redirect('dashboard')
        else:
            messages.error(request, "Invalid username or password.")
    else:
        form = AuthenticationForm()
    return render(request, 'login.html', {'form': form})

def logout_view(request):
    logout(request)
    return redirect('login')

@login_required
def dashboard_view(request):
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
    
    if user_role == 'ADMIN':
        shops = Shop.objects.all().order_by('name')
        if shop_filter_id:
            selected_shop = get_object_or_404(Shop, id=shop_filter_id)
    else:
        shops = Shop.objects.filter(id=user_shop.id) if user_shop else Shop.objects.none()
        selected_shop = user_shop

    # Base Querysets filtered by shop
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
    user_profile = request.user.profile
    user_shop = user_profile.shop
    user_is_admin = is_admin(request.user)
    
    if user_is_admin:
        shop_id = request.GET.get('shop_id')
        if shop_id:
            user_shop = get_object_or_404(Shop, id=shop_id)
        elif not user_shop:
            user_shop = Shop.objects.first() # Default to first shop if admin has none
            
    if not user_shop and not user_is_admin:
        messages.error(request, "Your profile is not assigned to any shop branch. Please contact an admin.")
        return redirect('dashboard')

    # Query products active in this shop
    # Expired items are excluded from sales!
    today = timezone.localdate()
    products_query = Product.objects.filter(is_active=True)
    if user_shop:
        products_query = products_query.filter(shop=user_shop)
    
    # Hide expired items
    products = products_query.exclude(expiry_date__lte=today).order_by('name')
    categories = Category.objects.all().order_by('name')
    settings = StoreSettings.get_settings()
    
    # Format products for JS search, adding expiry warning flags
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
            'expiry_date': p.expiry_date.strftime('%Y-%m-%d') if p.expiry_date else 'No Expiry'
        })

    context = {
        'products': products,
        'categories': categories,
        'settings': settings,
        'products_json': json.dumps(products_json),
        'is_admin': user_is_admin,
        'is_manager': is_manager(request.user),
        'user_shop': user_shop,
        'shops': Shop.objects.all().order_by('name') if user_is_admin else None,
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
            receipt_items.append({
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
    user_profile = request.user.profile
    user_role = user_profile.role
    user_shop = user_profile.shop
    is_user_admin = is_admin(request.user)

    categories = Category.objects.all().order_by('name')

    # Define inventory scope
    # Admin sees all but can filter by shop; Manager/Cashier restricted to their own shop
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
        
        # Categories (Admin only)
        if action == 'add_category':
            if not is_user_admin:
                messages.error(request, "Only Admins can manage categories.")
            else:
                name = request.POST.get('name')
                desc = request.POST.get('description')
                if name:
                    Category.objects.create(name=name, description=desc)
                    messages.success(request, f"Category '{name}' created successfully.")
            
        elif action == 'edit_category':
            if not is_user_admin:
                messages.error(request, "Only Admins can manage categories.")
            else:
                cat_id = request.POST.get('category_id')
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

            # Determine product shop binding
            if is_user_admin:
                prod_shop_id = request.POST.get('shop_id')
                prod_shop = get_object_or_404(Shop, id=prod_shop_id) if prod_shop_id else None
            else:
                prod_shop = user_shop

            if not prod_shop:
                messages.error(request, "Failed to create product. Shop assignment is required.")
                return redirect('products')

            category = Category.objects.get(id=cat_id) if cat_id else None

            if name and sku:
                if Product.objects.filter(sku=sku, is_active=True).exists():
                    messages.error(request, f"SKU/Barcode '{sku}' already exists.")
                else:
                    Product.objects.create(
                        name=name, sku=sku, category=category, cost_price=cost,
                        selling_price=sell, stock_quantity=qty, min_stock_level=min_lvl,
                        description=desc, expiry_date=expiry, shop=prod_shop
                    )
                    messages.success(request, f"Product '{name}' created successfully under '{prod_shop.name}'.")

        elif action == 'edit_product':
            prod_id = request.POST.get('product_id')
            # Fetch checking scope to prevent editing other shops' products if manager
            if is_user_admin:
                prod = get_object_or_404(Product, id=prod_id)
                prod_shop_id = request.POST.get('shop_id')
                if prod_shop_id:
                    prod.shop = get_object_or_404(Shop, id=prod_shop_id)
            else:
                prod = get_object_or_404(Product, id=prod_id, shop=user_shop)

            prod.name = request.POST.get('name')
            sku = request.POST.get('sku')
            if prod.sku != sku and Product.objects.filter(sku=sku, is_active=True).exists():
                messages.error(request, f"SKU/Barcode '{sku}' already exists.")
            else:
                prod.sku = sku
                cat_id = request.POST.get('category_id')
                prod.category = Category.objects.get(id=cat_id) if cat_id else None
                prod.cost_price = Decimal(request.POST.get('cost_price', 0.00))
                prod.selling_price = Decimal(request.POST.get('selling_price', 0.00))
                prod.stock_quantity = int(request.POST.get('stock_quantity', 0))
                prod.min_stock_level = int(request.POST.get('min_stock_level', 5))
                prod.description = request.POST.get('description')
                prod.expiry_date = request.POST.get('expiry_date') or None
                prod.save()
                messages.success(request, f"Product '{prod.name}' updated successfully.")

        elif action == 'delete_product':
            prod_id = request.POST.get('product_id')
            if is_user_admin:
                prod = get_object_or_404(Product, id=prod_id)
            else:
                prod = get_object_or_404(Product, id=prod_id, shop=user_shop)
            
            prod.is_active = False
            prod.save()
            messages.success(request, f"Product '{prod.name}' deleted successfully.")

        return redirect('products')

    context = {
        'products': products,
        'categories': categories,
        'is_admin': is_user_admin,
        'is_manager': is_manager(request.user),
        'shops': shops,
        'current_shop_filter': request.GET.get('shop_id', ''),
        'user_shop': user_shop,
    }
    return render(request, 'products.html', context)

@login_required
def sales_history_view(request):
    user_profile = request.user.profile
    user_role = user_profile.role
    user_shop = user_profile.shop
    is_user_admin = is_admin(request.user)

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
        sales = sales.filter(shop_id=shop_id)

    # Filter dropdowns lists
    if is_user_admin:
        cashiers = User.objects.filter(sales__isnull=False).distinct()
        shops = Shop.objects.all().order_by('name')
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

    if is_user_admin:
        sale = get_object_or_404(Sale, id=sale_id)
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
    user_profile = request.user.profile
    is_user_admin = is_admin(request.user)
    user_shop = user_profile.shop

    # Fetch users list based on role
    # Admin sees all; Manager sees only cashiers in their shop
    if is_user_admin:
        staff_profiles = UserProfile.objects.select_related('user', 'shop').all().order_by('role', 'user__username')
        shops = Shop.objects.all().order_by('name')
        roles_choices = UserProfile.ROLES
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

            # Create User with optional first/last name
            new_user = User.objects.create_user(
                username=username, email=email, password=password,
                first_name=first_name, last_name=last_name
            )
            # Create/Update profile
            profile = new_user.profile
            profile.role = role
            profile.shop = staff_shop
            profile.save()

            messages.success(request, f"Account for '{username}' ({profile.get_role_display()}) onboarded successfully.")

        elif action == 'edit_staff':
            profile_id = request.POST.get('profile_id')
            if is_user_admin:
                profile_to_edit = get_object_or_404(UserProfile, id=profile_id)
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
                    profile_to_edit.shop = get_object_or_404(Shop, id=shop_id)
                elif role == 'ADMIN':
                    profile_to_edit.shop = None

            profile_to_edit.save()
            messages.success(request, f"Profile for '{profile_to_edit.user.username}' updated successfully.")

        elif action == 'delete_staff':
            profile_id = request.POST.get('profile_id')
            if is_user_admin:
                profile_to_delete = get_object_or_404(UserProfile, id=profile_id)
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
    shops = Shop.objects.all().order_by('name')
    # Filter managers for selection (Admins or Managers)
    managers = User.objects.filter(profile__role__in=['ADMIN', 'MANAGER']).order_by('username')

    if request.method == 'POST' and 'action' in request.POST:
        action = request.POST.get('action')

        if action == 'add_shop':
            name = request.POST.get('name')
            location = request.POST.get('location')
            phone = request.POST.get('phone')
            manager_id = request.POST.get('manager_id')

            manager_user = get_object_or_404(User, id=manager_id) if manager_id else None

            if name:
                shop = Shop.objects.create(name=name, location=location, phone=phone, manager=manager_user)
                # Link manager's profile to this shop automatically if not linked
                if manager_user and not manager_user.profile.shop:
                    manager_user.profile.shop = shop
                    manager_user.profile.save()
                messages.success(request, f"Shop Branch '{name}' created successfully.")

        elif action == 'edit_shop':
            shop_id = request.POST.get('shop_id')
            shop = get_object_or_404(Shop, id=shop_id)

            shop.name = request.POST.get('name', shop.name)
            shop.location = request.POST.get('location', shop.location)
            shop.phone = request.POST.get('phone', shop.phone)
            
            manager_id = request.POST.get('manager_id')
            if manager_id:
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
