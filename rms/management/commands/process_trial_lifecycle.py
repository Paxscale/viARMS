"""
Management command: process_trial_lifecycle
==========================================
Scans all tenants and enforces viARMS subscription lifecycle rules:
1. After 14 days of Free Trial:
   - Account is automatically marked SUSPENDED.
   - An in-app AdminNotification is created.
   - An email notice is sent to the tenant warning of 30-day deletion countdown.
2. After 30 days of Suspension without redemption:
   - Account and all associated store data are PERMANENTLY DELETED.

Usage:
  python manage.py process_trial_lifecycle
"""

from django.core.management.base import BaseCommand
from rms.views import process_all_tenants_lifecycle


class Command(BaseCommand):
    help = "Enforces 14-day trial suspension and 30-day auto-deletion for unredeemed tenant accounts."

    def handle(self, *args, **options):
        self.stdout.write("Processing tenant subscription lifecycles...")
        suspended_count, deleted_count = process_all_tenants_lifecycle()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Newly suspended: {suspended_count}, Auto-deleted after 30 days: {deleted_count}."
            )
        )
