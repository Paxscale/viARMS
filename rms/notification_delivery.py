"""Helpers for creating durable in-app notifications and queued email."""

from django.contrib.auth.models import User
from django.db import transaction

from .models import AdminNotification, NotificationEmail


@transaction.atomic
def create_admin_notification(*, title, message, notification_type='INFO',
                              target_tenant=None, sender=None):
    """Save a notification and queue one email for each intended tenant owner."""
    notification = AdminNotification.objects.create(
        title=title,
        message=message,
        notification_type=notification_type,
        target_tenant=target_tenant,
        sender=sender,
    )

    if target_tenant:
        recipients = User.objects.filter(pk=target_tenant.pk, is_active=True)
    else:
        recipients = User.objects.filter(
            is_active=True,
            is_superuser=False,
            profile__tenant_owner__isnull=True,
        )

    emails = set(recipients.exclude(email='').values_list('email', flat=True))
    subject = f"viARMS: {title}"
    email_body = (
        f"{title}\n\n{message}\n\n"
        "You can view this announcement in the Notifications area of your viARMS account."
    )
    NotificationEmail.objects.bulk_create([
        NotificationEmail(
            notification=notification,
            recipient=email,
            subject=subject,
            message=email_body,
        )
        for email in emails
    ])
    return notification
