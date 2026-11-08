from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing_view, name='landing'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('admin-panel/', views.admin_panel_view, name='admin_panel'),
    path('api/ads/', views.ads_api, name='ads_api'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('pos/', views.pos_view, name='pos'),
    path('pos/checkout/', views.checkout_api, name='checkout_api'),
    path('products/', views.product_list_view, name='products'),
    path('sales/', views.sales_history_view, name='sales_history'),
    path('sales/<int:sale_id>/json/', views.sale_detail_json, name='sale_detail_json'),
    path('sales/export/', views.export_sales_csv, name='export_sales_csv'),
    path('settings/', views.settings_view, name='settings'),
    path('staff/', views.staff_management_view, name='staff_management'),
    path('pos/customer-display/', views.customer_display_view, name='customer_display'),
    path('shops/', views.shop_management_view, name='shops'),
]

