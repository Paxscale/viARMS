"""Helpers for recording concise business activity without storing files."""

from .models import ActivityLog


def log_business_activity(*, actor, action, details, tenant_owner=None):
    if tenant_owner is None and actor:
        profile = getattr(actor, 'profile', None)
        if profile:
            tenant_owner = profile.tenant_owner or actor
        elif not actor.is_superuser:
            tenant_owner = actor
    if tenant_owner is None:
        return None
    return ActivityLog.objects.create(
        tenant_owner=tenant_owner,
        actor=actor,
        action=action[:60],
        details=str(details)[:500],
    )
