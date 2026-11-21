from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import User

from .models import (
    AccountAppeal,
    ActivityLog,
    AdminNotification,
    Advertisement,
    NotificationEmail,
    NotificationReadStatus,
    Shop,
    SubscriptionPayment,
    UserProfile,
)


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    fk_name = 'user'
    can_delete = False
    extra = 0
    fields = (
        'role', 'tenant_owner', 'tenant_type', 'plan', 'is_approved',
        'kyc_status', 'onboarding_completed', 'business_type',
        'trial_start_date', 'subscription_expires_at', 'is_suspended',
        'suspension_reason', 'suspended_at',
    )


class UserAdmin(DjangoUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_active', 'is_staff', 'date_joined')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'date_joined')


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'tenant_owner', 'tenant_type', 'plan', 'is_approved', 'is_suspended')
    list_filter = ('role', 'tenant_type', 'plan', 'is_approved', 'is_suspended', 'kyc_status')
    search_fields = ('user__username', 'user__email', 'tenant_owner__username', 'tenant_owner__email')
    list_select_related = ('user', 'tenant_owner')
    autocomplete_fields = ('user', 'tenant_owner', 'shop')


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    verbose_name_plural = 'Shops (Tenants)'
    list_display = ('name', 'tenant_owner', 'manager', 'location', 'created_at')
    search_fields = ('name', 'location', 'tenant_owner__username', 'tenant_owner__email')
    list_filter = ('created_at',)
    list_select_related = ('tenant_owner', 'manager')
    autocomplete_fields = ('tenant_owner', 'manager')


@admin.register(AccountAppeal)
class AccountAppealAdmin(admin.ModelAdmin):
    list_display = ('user', 'status', 'created_at', 'reviewed_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username', 'user__email', 'reason', 'admin_response')
    list_select_related = ('user',)
    autocomplete_fields = ('user',)
    readonly_fields = ('created_at', 'reviewed_at')


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('tenant_owner', 'actor', 'action', 'details', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('tenant_owner__username', 'tenant_owner__email', 'actor__username', 'details')
    list_select_related = ('tenant_owner', 'actor')
    autocomplete_fields = ('tenant_owner', 'actor')
    date_hierarchy = 'created_at'


@admin.register(AdminNotification)
class AdminNotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'notification_type', 'target_tenant', 'sender', 'is_active', 'created_at')
    list_filter = ('notification_type', 'is_active', 'created_at')
    search_fields = ('title', 'message', 'target_tenant__username', 'target_tenant__email')
    list_select_related = ('target_tenant', 'sender')
    autocomplete_fields = ('target_tenant', 'sender')


@admin.register(Advertisement)
class AdvertisementAdmin(admin.ModelAdmin):
    list_display = ('title', 'shop', 'is_active', 'display_order', 'created_at')
    list_filter = ('is_active', 'shop', 'created_at')
    search_fields = ('title', 'content', 'shop__name')
    list_select_related = ('shop',)
    autocomplete_fields = ('shop',)


@admin.register(NotificationEmail)
class NotificationEmailAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'status', 'attempts', 'created_at', 'sent_at')
    list_filter = ('status', 'created_at')
    search_fields = ('recipient', 'subject', 'message')
    readonly_fields = ('created_at', 'sent_at', 'last_error')
    date_hierarchy = 'created_at'


@admin.register(NotificationReadStatus)
class NotificationReadStatusAdmin(admin.ModelAdmin):
    list_display = ('notification', 'user', 'read_at')
    search_fields = ('notification__title', 'user__username', 'user__email')
    list_select_related = ('notification', 'user')
    autocomplete_fields = ('notification', 'user')


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ('reference', 'tenant', 'plan', 'billing_cycle', 'amount', 'status', 'created_at')
    list_filter = ('plan', 'billing_cycle', 'payment_method', 'status', 'created_at')
    search_fields = ('reference', 'tenant__username', 'tenant__email')
    list_select_related = ('tenant', 'confirmed_by')
    autocomplete_fields = ('tenant', 'confirmed_by')
    date_hierarchy = 'created_at'
