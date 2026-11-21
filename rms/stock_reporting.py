"""Persistent stock-in audit records and email queue helpers."""

from django.contrib.auth.models import User
from django.db import transaction

from .models import StockInReport, StockMovement
from .activity_logging import log_business_activity


@transaction.atomic
def record_stock_additions(additions, *, actor, tenant_owner=None):
    """Record positive quantity changes and queue one report email for the batch.

    ``additions`` is an iterable of ``(product, quantity_before)`` pairs.
    """
    additions = [
        (product, int(quantity_before))
        for product, quantity_before in additions
        if product.stock_quantity > int(quantity_before)
    ]
    if not additions:
        return None

    owner = tenant_owner
    if owner is None:
        owner = next((product.shop.tenant_owner for product, _ in additions
                      if product.shop_id and product.shop.tenant_owner_id), None)
    if owner is None and actor and hasattr(actor, 'profile'):
        owner = actor.profile.tenant_owner or actor
    if owner is None:
        owner = actor

    report = StockInReport.objects.create(
        tenant_owner=owner,
        actor=actor,
        recipient=(owner.email or '').strip() if owner else '',
    )
    StockMovement.objects.bulk_create([
        StockMovement(
            report=report,
            product=product,
            product_name=product.name,
            sku=product.sku,
            category_name=product.category.name if product.category_id else '',
            shop_name=product.shop.name if product.shop_id else '',
            batch_number=product.batch_number or '',
            quantity_before=before,
            quantity_added=product.stock_quantity - before,
            quantity_after=product.stock_quantity,
            unit_cost=product.cost_price,
        )
        for product, before in additions
    ])
    for product, before in additions:
        log_business_activity(
            actor=actor,
            tenant_owner=owner,
            action='Stock added',
            details=f"{product.name}: {product.stock_quantity - before} added; stock is now {product.stock_quantity} at {product.shop.name if product.shop_id else 'Unassigned shop'}.",
        )
    return report
