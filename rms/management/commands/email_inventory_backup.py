"""Queue categorized Excel inventory snapshots for tenant admins."""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from rms.models import StockInReport


class Command(BaseCommand):
    help = "Queue an in-memory Excel inventory backup email for each tenant admin."

    def handle(self, *args, **options):
        owners = User.objects.filter(
            is_active=True,
            is_superuser=False,
        ).filter(
            Q(profile__tenant_owner=F('pk'))
            | Q(profile__tenant_owner__isnull=True, profile__role='ADMIN')
        ).exclude(email='').distinct()

        queued = 0
        for owner in owners:
            StockInReport.objects.create(
                tenant_owner=owner,
                recipient=owner.email.strip(),
                include_inventory_snapshot=True,
                next_attempt_at=timezone.now(),
            )
            queued += 1

        self.stdout.write(f"Full inventory backup emails queued: {queued}.")
        self.stdout.write("Run send_notification_emails to deliver the queued workbooks.")
