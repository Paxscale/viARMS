from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('pos/', views.pos_view, name='pos'),
    path('pos/checkout/', views.checkout_api, name='checkout_api'),
    path('products/', views.product_list_view, name='products'),
    path('sales/', views.sales_history_view, name='sales_history'),
    path('sales/<int:sale_id>/json/', views.sale_detail_json, name='sale_detail_json'),
    path('sales/export/', views.export_sales_csv, name='export_sales_csv'),
    path('settings/', views.settings_view, name='settings'),
    path('staff/', views.staff_management_view, name='staff_management'),
    path('shops/', views.shop_management_view, name='shops'),
]

