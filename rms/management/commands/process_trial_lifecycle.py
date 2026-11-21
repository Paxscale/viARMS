"""
Management command: process_trial_lifecycle
==========================================
Scans all tenants and enforces viARMS subscription lifecycle rules:
1. After 14 days of Free Trial, store operations are suspended and an in-app notice is created.
2. Suspended accounts and business data are retained until an administrator explicitly deletes them.

Usage:
  python manage.py process_trial_lifecycle
"""

from django.core.management.base import BaseCommand
from rms.views import process_all_tenants_lifecycle


class Command(BaseCommand):
    help = "Suspends expired trial accounts while retaining their business data."

    def handle(self, *args, **options):
        self.stdout.write("Processing tenant subscription lifecycles...")
        suspended_count, deleted_count = process_all_tenants_lifecycle()
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Newly suspended: {suspended_count}. Tenant data was retained."
            )
        )
