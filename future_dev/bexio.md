# bexio integration (idea, not started)

Status: parked, 2026-11-11. Build only if guides ask for it.

## What bexio is

Swiss online business software for small companies: contacts, quotes, invoices
with QR-bill, reminders, bookkeeping, VAT returns, bank link, payroll, time
tracking. Common among Swiss self-employed people and their Treuhänder.
Has a REST API; each user logs in with their own bexio account (OAuth).

(Written from general knowledge; the bexio website and API docs have not been
checked yet.)

## What Tourchef already covers

- Invoices with line items and expenses (`bookings/models.py`, `InvoiceLine`)
- VAT number and rate, IBAN for bank transfer (`accounts/models.py`)
- Card payments via Stripe, receipts by email
- Income/expense export as CSV or Excel per period (`bookings/export.py`)

So guides can already get their numbers out. bexio would only save re-typing.

## What an integration would be

Optional, per guide. Guides who don't connect bexio see no change and keep
using the CSV/Excel export. Guides can connect or disconnect at any time.

One-way push, Tourchef → bexio:

1. Guide connects their bexio account once in settings ("Connect bexio"
   button; off by default).
2. When a booking is invoiced: create the guest as a bexio contact, create the
   invoice.
3. When the guest pays (Stripe or bank): mark the invoice paid in bexio.

## Cost and risk

- Roughly 1–2 weeks of work.
- Register an app with bexio, keep the login connection alive (token refresh).
- Handle bexio being down (retry queue).
- Hard part: keeping both sides in sync when an invoice changes after it was
  sent.

## Guide answer (questionnaire Q7, 2026-11-11)

For now the export includes everything. Details get decided when we work on
the bookkeeping side: either connect to something like bexio, or shape the
export together with a Treuhänder so it suits further processing, e.g. as an
import file for accounting software such as bexio.

## Decision rule

- Ask in the Bergführer questionnaire: "Which accounting software do you use
  (bexio, Excel, Treuhänder, other)?"
- If 2–3 or more guides say bexio: build the one-way push.
- Otherwise: keep the CSV/Excel export. Possible cheap step: an export in a
  format bexio can import (check which import formats bexio accepts first).
