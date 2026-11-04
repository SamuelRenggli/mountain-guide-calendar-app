"""R-203: the guide's income and expenses for a period, as CSV or Excel.

One row per payment received in the period (custom tours after the invoice, registrations at signup).
"Spesen" are the expense lines the guide billed to the guest; refunds are subtracted.
Contains no phone numbers, emergency contacts or experience — only what bookkeeping needs.
"""

import csv
import io
from datetime import date
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Booking, InvoiceLine, money


def rows(guide, start: date, end: date) -> tuple[list[str], list[list]]:
    header = [_("Datum"), _("Beleg-Nr."), _("Art"), _("Tour"), _("Tourdatum"), _("Gast"), _("Betrag CHF"),
              _("davon Spesen CHF"), _("Zurückbezahlt CHF"), _("Netto CHF"), _("MWST CHF"), _("Zahlungsart")]
    paid = (Booking.objects.filter(guide=guide, paid_at__date__gte=start, paid_at__date__lte=end)
            .select_related("departure", "tour").prefetch_related("invoice_lines").order_by("paid_at"))
    data = []
    for b in paid:
        expenses = sum((line.amount for line in b.invoice_lines.all() if line.kind == InvoiceLine.Kind.EXPENSE),
                       Decimal(0))
        net = money(b.amount_paid - b.refunded_amount)
        vat = b.vat_included(net)
        data.append([
            timezone.localtime(b.paid_at).date(), b.pk, _("Termin") if b.is_registration else _("Tour auf Anfrage"),
            b.title, b.start_date, b.guest_name, money(b.amount_paid), money(expenses), money(b.refunded_amount), net,
            vat if vat is not None else "", b.get_payment_method_display(),
        ])
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
        for cell in row[6:11]:
            cell.number_format = "#,##0.00"
    if data:
        total_row = len(data) + 2
        ws.cell(total_row, 1, _("Total")).font = Font(bold=True)
        for col in (7, 8, 9, 10):
            letter = ws.cell(1, col).column_letter
            ws.cell(total_row, col, f"=SUM({letter}2:{letter}{total_row - 1})").number_format = "#,##0.00"
    for col, width in zip("ABCDEFGHIJKL", (12, 10, 16, 32, 12, 24, 12, 14, 16, 12, 10, 18)):
        ws.column_dimensions[col].width = width
    buffer = io.BytesIO()
    wb.save(buffer)
    resp = HttpResponse(buffer.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
    return resp
