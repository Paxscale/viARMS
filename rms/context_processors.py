from django.db.models import Q
from django.conf import settings
from .models import AdminNotification, NotificationReadStatus


def user_roles(request):
    """
    Global context processor that injects role flags and tenant notifications
    into every template automatically.
    """
    is_admin = False
    is_manager = False
    tenant_notifications = []
    unread_notifications_count = 0
    has_urgent_notification = False
    urgent_notification = None
    wholesale_access = False

    if request.user.is_authenticated:
        try:
            owner = request.user.profile.tenant_owner or request.user
            wholesale_access = (
                owner.profile.plan != 'STANDARD'
                and owner.profile.tenant_type in {'WHOLESALE', 'RETAIL_WHOLESALE'}
            )
        except Exception:
            wholesale_access = False

        is_admin = request.user.is_superuser or (
            hasattr(request.user, 'profile') and request.user.profile.role == 'ADMIN'
        )
        is_manager = hasattr(request.user, 'profile') and request.user.profile.role == 'MANAGER'

        # Notifications for this user/tenant
        try:
            tenant_user = request.user
            if hasattr(request.user, 'profile') and request.user.profile.tenant_owner:
                tenant_user = request.user.profile.tenant_owner

            # Active notifications matching tenant or broadcast
            qs = AdminNotification.objects.filter(is_active=True).filter(
                Q(target_tenant__isnull=True) | Q(target_tenant=tenant_user) | Q(target_tenant=request.user)
            ).order_by('-created_at')[:20]

            read_notif_ids = set(NotificationReadStatus.objects.filter(
                user=request.user
            ).values_list('notification_id', flat=True))

            for notif in qs:
                is_read = notif.id in read_notif_ids
                if not is_read:
                    unread_notifications_count += 1
                    if notif.notification_type == 'URGENT' and not urgent_notification:
                        urgent_notification = notif
                tenant_notifications.append({
                    'id': notif.id,
                    'title': notif.title,
                    'message': notif.message,
                    'type': notif.notification_type,
                    'type_display': notif.get_notification_type_display(),
                    'created_at': notif.created_at,
                    'is_read': is_read,
                    'target_is_all': notif.target_tenant is None,
                })
        except Exception:
            tenant_notifications = []
            unread_notifications_count = 0
            has_urgent_notification = False
            urgent_notification = None

    return {
        'is_admin': is_admin,
        'is_manager': is_manager,
        'tenant_notifications': tenant_notifications,
        'unread_notifications_count': unread_notifications_count,
        'has_urgent_notification': urgent_notification is not None,
        'urgent_notification': urgent_notification,
        'wholesale_access': wholesale_access,
    }


def social_login_options(request):
    return {
        'google_login_enabled': bool(
            getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', '')
            and getattr(settings, 'GOOGLE_OAUTH_CLIENT_SECRET', '')
        ),
        'apple_login_enabled': bool(
            getattr(settings, 'APPLE_SERVICE_ID', '')
            and getattr(settings, 'APPLE_KEY_ID', '')
            and getattr(settings, 'APPLE_TEAM_ID', '')
            and getattr(settings, 'APPLE_PRIVATE_KEY', '')
        ),
    }
