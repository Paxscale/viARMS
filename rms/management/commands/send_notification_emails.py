"""Deliver queued notification emails and retain failures for retry."""

from datetime import timedelta
from io import BytesIO

from django.core.mail import EmailMessage, send_mail
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from rms.models import NotificationEmail, Product, StockInReport


class Command(BaseCommand):
    help = "Send queued notification emails, retrying transient failures."

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--retry-failed', action='store_true')

    def handle(self, *args, **options):
        if options['retry_failed']:
            NotificationEmail.objects.filter(status='FAILED').update(
                status='PENDING', attempts=0, last_error='', next_attempt_at=timezone.now()
            )
            StockInReport.objects.filter(status='FAILED').update(
                status='PENDING', attempts=0, last_error='', next_attempt_at=timezone.now()
            )

        sent = failed = 0
        due_ids = list(NotificationEmail.objects.filter(
            status='PENDING', next_attempt_at__lte=timezone.now()
        ).order_by('created_at').values_list('id', flat=True)[:max(1, options['limit'])])

        for email_id in due_ids:
            with transaction.atomic():
                delivery = NotificationEmail.objects.select_for_update().filter(
                    pk=email_id, status='PENDING', next_attempt_at__lte=timezone.now()
                ).first()
                if not delivery:
                    continue
                delivery.attempts += 1
                try:
                    accepted = send_mail(
                        delivery.subject,
                        delivery.message,
                        None,
                        [delivery.recipient],
                        fail_silently=False,
                    )
                    if accepted != 1:
                        raise RuntimeError('The configured email backend did not accept the message.')
                    delivery.status = 'SENT'
                    delivery.sent_at = timezone.now()
                    delivery.last_error = ''
                    sent += 1
                except Exception as exc:
                    delivery.last_error = str(exc)[:2000]
                    if delivery.attempts >= 5:
                        delivery.status = 'FAILED'
                        failed += 1
                    else:
                        # Retry after 2, 4, 8, then 16 minutes.
                        delivery.next_attempt_at = timezone.now() + timedelta(
                            minutes=2 ** delivery.attempts
                        )
                delivery.save(update_fields=[
                    'attempts', 'status', 'sent_at', 'last_error', 'next_attempt_at'
                ])

        remaining = NotificationEmail.objects.filter(status='PENDING').count()
        stock_sent, stock_failed = self._send_stock_reports(
            limit=max(1, options['limit'])
        )
        stock_pending = StockInReport.objects.filter(status='PENDING').count()
        self.stdout.write(
            f"Notification emails sent: {sent}; permanently failed: {failed}; pending: {remaining}. "
            f"Stock reports sent: {stock_sent}; permanently failed: {stock_failed}; pending: {stock_pending}."
        )

    def _send_stock_reports(self, *, limit):
        sent = failed = 0
        due_ids = list(StockInReport.objects.filter(
            status='PENDING', next_attempt_at__lte=timezone.now()
        ).order_by('created_at').values_list('id', flat=True)[:limit])
        for report_id in due_ids:
            with transaction.atomic():
                report = StockInReport.objects.select_for_update().filter(
                    pk=report_id, status='PENDING', next_attempt_at__lte=timezone.now()
                ).first()
                if not report:
                    continue
                report.attempts += 1
                try:
                    if not report.recipient:
                        raise RuntimeError('The tenant admin account has no email address.')
                    movements = list(report.movements.all())
                    if not movements and not report.include_inventory_snapshot:
                        raise RuntimeError('The stock report has no stock movement rows.')
                    attachment = self._build_stock_workbook(report, movements)
                    if report.include_inventory_snapshot:
                        subject = 'viARMS inventory backup'
                        body = (
                            "Attached is the current categorized inventory snapshot for your viARMS account. "
                            "It includes quantities, unit costs, total cost values, selling prices, and retail values."
                        )
                    else:
                        first = movements[0]
                        subject = f"viARMS stock updated: {first.product_name}"
                        if len(movements) > 1:
                            subject += f" and {len(movements) - 1} other item(s)"
                        body = (
                            "A stock addition was recorded in your viARMS account.\n\n"
                            "The attached workbook contains the stock-in details and added stock cost value."
                        )
                    email = EmailMessage(subject, body, None, [report.recipient])
                    email.attach(
                        f"viarms_inventory_{report.created_at:%Y%m%d_%H%M%S}_{report.pk}.xlsx",
                        attachment,
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    )
                    accepted = email.send(fail_silently=False)
                    if accepted != 1:
                        raise RuntimeError('The configured email backend did not accept the message.')
                    report.status = 'SENT'
                    report.sent_at = timezone.now()
                    report.last_error = ''
                    sent += 1
                except Exception as exc:
                    report.last_error = str(exc)[:2000]
                    if report.attempts >= 5:
                        report.status = 'FAILED'
                        failed += 1
                    else:
                        report.next_attempt_at = timezone.now() + timedelta(
                            minutes=2 ** report.attempts
                        )
                report.save(update_fields=[
                    'attempts', 'status', 'sent_at', 'last_error', 'next_attempt_at'
                ])
        return sent, failed

    @staticmethod
    def _build_stock_workbook(report, movements):
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        workbook = Workbook()
        sheet = workbook.active
        if report.include_inventory_snapshot:
            sheet.title = 'Inventory by Category'
            sheet.append(['Inventory snapshot created', timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S')])
            sheet.append([])
            sheet.append([
                'Category', 'Product', 'SKU', 'Batch', 'Shop', 'Last updated', 'Quantity',
                'Unit cost', 'Cost value', 'Unit selling price', 'Retail value',
            ])
            products = Product.objects.filter(is_active=True).select_related('category', 'shop')
            if report.tenant_owner_id:
                products = products.filter(shop__tenant_owner_id=report.tenant_owner_id)
            for product in products.order_by('category__name', 'name', 'shop__name').iterator():
                quantity = product.stock_quantity
                sheet.append([
                    product.category.name if product.category_id else 'Uncategorized',
                    product.name, product.sku, product.batch_number or '',
                    product.shop.name if product.shop_id else '',
                    timezone.localtime(product.updated_at).strftime('%Y-%m-%d %H:%M:%S'),
                    quantity, float(product.cost_price), float(product.cost_price * quantity),
                    float(product.selling_price), float(product.selling_price * quantity),
                ])
        else:
            sheet.title = 'Stock In'
            sheet.append(['Stock added', timezone.localtime(report.created_at).strftime('%Y-%m-%d %H:%M:%S')])
            sheet.append([])
            sheet.append([
                'Category', 'Product', 'SKU', 'Batch', 'Shop', 'Date added',
                'Quantity before', 'Quantity added', 'Quantity after', 'Unit cost', 'Added stock cost value',
            ])
            for movement in movements:
                sheet.append([
                    movement.category_name or 'Uncategorized', movement.product_name, movement.sku,
                    movement.batch_number, movement.shop_name,
                    timezone.localtime(movement.created_at).strftime('%Y-%m-%d %H:%M:%S'),
                    movement.quantity_before, movement.quantity_added, movement.quantity_after,
                    float(movement.unit_cost), float(movement.unit_cost * movement.quantity_added),
                ])

        sheet.freeze_panes = 'A4'
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[3]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='315E81')
        for col_cells in sheet.columns:
            width = min(max(max(len(str(cell.value or '')) for cell in col_cells) + 2, 12), 34)
            sheet.column_dimensions[get_column_letter(col_cells[0].column)].width = width

        output = BytesIO()
        workbook.save(output)
        return output.getvalue()
