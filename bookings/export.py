"""R-203: the guide's income and expenses for a period, as CSV or Excel.

One row per payment received in the period (custom tours after the invoice, registrations at signup) and one row per
refund sent in the period, as a negative amount, in the month it happened (questionnaire Q7: "include everything for
now"; the final format gets decided with a Treuhänder, see future_dev/bexio.md).
"Spesen" are the expense lines the guide billed to the guest. "Tourchef-Gebühr" is the platform fee on online
payments; "Auszahlung" is what reaches the guide's account. "Offene Rückzahlung" is decided but not yet sent.
Contains no phone numbers, emergency contacts or experience — only what bookkeeping needs.
"""

import csv
import io
from datetime import date
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Booking, InvoiceLine, Refund, cents, money

def _fee(booking: Booking) -> Decimal:
    """Tourchef's fee on an online payment (refunded proportionally with a refund)."""
    if booking.payment_method != Booking.PaymentMethod.STRIPE or not booking.amount_paid:
        return Decimal("0.00")
    from payments.stripe_service import platform_fee_cents

    return money(Decimal(platform_fee_cents(cents(booking.amount_paid))) / 100)


def rows(guide, start: date, end: date) -> tuple[list[str], list[list]]:
    header = [_("Datum"), _("Beleg-Nr."), _("Art"), _("Tour"), _("Tourdatum"), _("Gast"), _("Betrag CHF"),
              _("davon Spesen CHF"), _("Tourchef-Gebühr CHF"), _("Auszahlung an dich CHF"),
              _("Offene Rückzahlung CHF"), _("MWST CHF"), _("Zahlungsart")]
    data = []
    paid = (Booking.objects.filter(guide=guide, paid_at__date__gte=start, paid_at__date__lte=end)
            .select_related("departure", "tour").prefetch_related("invoice_lines"))
    for b in paid:
        expenses = sum((line.amount for line in b.invoice_lines.all() if line.kind == InvoiceLine.Kind.EXPENSE),
                       Decimal(0))
        fee = _fee(b)
        vat = b.vat_included(b.amount_paid)
        data.append([
            timezone.localtime(b.paid_at), b.pk, _("Termin") if b.is_registration else _("Tour auf Anfrage"),
            b.title, b.start_date, b.guest_name, money(b.amount_paid), money(expenses), fee,
            money(b.amount_paid - fee), money(b.refund_due), vat if vat is not None else "",
            b.get_payment_method_display(),
        ])
    refunds = (Refund.objects.filter(booking__guide=guide, created_at__date__gte=start, created_at__date__lte=end)
               .select_related("booking__departure", "booking__tour"))
    for r in refunds:
        b = r.booking
        total_fee = _fee(b)
        fee = money(total_fee * r.amount / b.amount_paid) if b.amount_paid else Decimal("0.00")
        vat = b.vat_included(r.amount)
        data.append([
            timezone.localtime(r.created_at), b.pk, _("Rückzahlung"), b.title, b.start_date, b.guest_name,
            -money(r.amount), Decimal("0.00"), -fee, -money(r.amount - fee), Decimal("0.00"),
            -vat if vat is not None else "", b.get_payment_method_display(),
        ])
    data.sort(key=lambda row: row[0])
    for row in data:
        row[0] = row[0].date()
    return header, data


def response(guide, start: date, end: date, fmt: str) -> HttpResponse:
    header, data = rows(guide, start, end)
    name = f"tourchef-{start:%Y%m%d}-{end:%Y%m%d}"
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")  # Excel in Switzerland expects semicolons
        writer.writerow(header)
        for row in data:
            writer.writerow([f"{v:%d.%m.%Y}" if isinstance(v, date) else v for v in row])
        resp = HttpResponse("﻿" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
        return resp

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = _("Einnahmen")
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in data:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in row])
    for row in ws.iter_rows(min_row=2):
        row[0].number_format = row[4].number_format = "DD.MM.YYYY"
        for cell in row[6:12]:
            cell.number_format = "#,##0.00"
    if data:
        total_row = len(data) + 2
        ws.cell(total_row, 1, _("Total")).font = Font(bold=True)
        for col in (7, 8, 9, 10, 11, 12):
            letter = ws.cell(1, col).column_letter
            ws.cell(total_row, col, f"=SUM({letter}2:{letter}{total_row - 1})").number_format = "#,##0.00"
    for col, width in zip("ABCDEFGHIJKLM", (12, 10, 16, 32, 12, 24, 12, 14, 16, 18, 18, 10, 18)):
        ws.column_dimensions[col].width = width
    buffer = io.BytesIO()
    wb.save(buffer)
    resp = HttpResponse(buffer.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
    return resp
