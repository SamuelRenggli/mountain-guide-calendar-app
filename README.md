# Tourchef – Buchungen für Bergführer

Mountain guides embed a booking widget on their website. Guests either **sign up for a scheduled date**
(Termin: fixed price per person, paid right away by card/TWINT) or **request their own tour** on fixed dates
(the guide confirms or counter-offers and bills after the tour).

Django 5.2 LTS · PostgreSQL 16 · server-rendered templates (no JS framework) · Stripe Connect Express ·
Google Cloud Run. Requirements: `../konzept/11-requirements-pilot.md` (R-xxx numbers in the code refer to it),
data protection: `../konzept/12-datenschutz.md`.

Widget, guest page, dashboard, legal pages and all emails are in **German, French, Italian and English**.
Guests get the browser language (switchable, remembered in a cookie) and emails in the language they used;
guides get their profile language.

Open tasks before going live: [ToDo.md](ToDo.md). Pilot test: [TEST_PLAN.md](TEST_PLAN.md).

## Flows

**Scheduled date (Termin)**

```
guide publishes ──▶ open ──guide confirms──▶ confirmed
  (days blocked for requests)  └─ guide cancels / decision deadline passes ──▶ cancelled, everyone refunded in full
                               └─ guide moves it (weather) ──▶ guests accept, or cancel with a full refund

guest signs up ──▶ payment_pending (seats held 35 min) ──Stripe paid──▶ paid ──guest cancels──▶ cancelled
                     └─ payment fails / times out ──▶ expired                (fee per terms, rest refunded)
full? ──▶ waiting list; a freed seat emails everyone on it at once, first to pay gets it
```

**Custom tour request**

```
request (fixed dates, days held) ──▶ requested
   ├─ confirm (price + optional split by guide/aspirant + estimated expenses) ──▶ confirmed
   ├─ counter-offer ──▶ offered ──▶ accept ──▶ confirmed / decline ──▶ declined
   └─ reject ──▶ rejected
confirmed ──guide edits (dates, group, price)──▶ confirmed, guest gets the change by email
confirmed ──weather: replacement date──▶ reschedule ──accept──▶ confirmed / no or no answer ──▶ cancelled
                                                    (or billed: costs the guide already had)
confirmed ──after the tour: invoice (tour price + expenses, VAT)──▶ awaiting_payment ──▶ paid
```

Deadlines: guide answers within 5 days, guest within 5 days (`REQUEST_RESPONSE_DAYS`, `GUEST_RESPONSE_DAYS`).
Cancellation template (per guide adjustable): free until 14 days, 50 % until 7 days, then 100 %; frozen per
booking when the guest accepts the terms. A PostgreSQL exclusion constraint makes double-booking requests
impossible; signups lock the date row so seats are never oversold.

The hourly job (`manage.py expire_bookings`, Cloud Scheduler) expires overdue requests, releases unpaid seats,
reminds guides 3 h before a decision deadline, cancels undecided dates and retries failed refunds.

## Project layout

| Path | What |
|---|---|
| `config/` | settings (all from env vars), URLs |
| `core/` | encrypted model field, language and two-factor middleware, CSP, rate limit, legal pages |
| `accounts/` | guides: email login, TOTP two-factor, re-authentication, rate tiers, access log |
| `bookings/models.py` | Tour (template), Departure (Termin), Booking (request or registration), WaitlistEntry, … |
| `bookings/services.py` | request state machine and cancellation rules — every status change goes through here |
| `bookings/departures.py` | scheduled dates: signup, payment, waitlist, cancel/move, refunds, hourly jobs, data deletion |
| `bookings/availability.py` | which days can be requested (rules, blocks, calendar, bookings, scheduled dates) |
| `bookings/emails.py` | every email, as translatable strings |
| `bookings/export.py` | income/expense export (CSV, Excel) |
| `bookings/views_widget.py` | widget `/w/<slug>/`, scheduled date `/t/<id>/`, guide terms `/w/<slug>/bedingungen/` |
| `bookings/views_guest.py` | guest page `/b/<secret-token>/` |
| `bookings/views_dashboard.py` | guide dashboard `/dashboard/`, pilot stats for staff |
| `payments/` | Stripe Connect onboarding, Checkout, refunds, webhook |
| `templates/legal/` | privacy policy, legal notice, terms of use — **drafts**, one file per language |
| `locale/` | FR/IT/EN translations (source language German) |

## Local development

```bash
python -m venv .venv && .venv/Scripts/activate      # Windows (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
docker run -d --name tourchef-pg -e POSTGRES_USER=tourchef -e POSTGRES_PASSWORD=dev \
  -e POSTGRES_DB=tourchef -p 127.0.0.1:5433:5432 postgres:16-alpine
```

`.env` for development:

```
DEBUG=true
DATABASE_URL=postgres://tourchef:dev@127.0.0.1:5433/tourchef
GUIDE_SIGNUP_CODE=test
# optional, skips the two-factor setup locally (staff still need it)
REQUIRE_GUIDE_2FA=false
```

```bash
python manage.py migrate
python manage.py createcachetable
python manage.py createsuperuser
python manage.py runserver
pytest
```

With `DEBUG=true` and no `FIELD_ENCRYPTION_KEYS`, a development key is derived from `SECRET_KEY`.
Without `EMAIL_HOST`, emails are printed to the console. Scheduled dates need Stripe test keys
(guests can only sign up by paying).

### Translations

Source strings are German. After changing texts:

```bash
python manage.py makemessages -l fr -l it -l en --ignore=.venv --ignore=tests --no-location
# translate the new/empty msgstr entries in locale/*/LC_MESSAGES/django.po
python manage.py compilemessages --ignore=.venv
```

The compiled `.mo` files are committed, so the image and CI need no gettext. `--no-location` avoids a
Windows path bug in `makemessages`.

## Deployment

Cloud Run, see [deploy/README.md](deploy/README.md). Required in the production config besides the old values:
`FIELD_ENCRYPTION_KEYS` (the app refuses to start without it), `OPERATOR_NAME`, `OPERATOR_ADDRESS`,
`PRIVACY_EMAIL`.

### Stripe

1. Connect enabled (Express accounts, Switzerland). Turn on cards and **TWINT** under Settings → Payment
   methods, also for connected accounts. Checkout offers only `STRIPE_PAYMENT_METHODS` (default `card,twint`).
2. Webhook to `<SITE_URL>/zahlungen/stripe/webhook/`:
   - "Your account": `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
     `checkout.session.expired`, `checkout.session.async_payment_failed` → `STRIPE_WEBHOOK_SECRET`
   - "Connected accounts": `account.updated` → `STRIPE_CONNECT_WEBHOOK_SECRET`
3. Guides click "Mit Stripe verbinden" in their settings. Payments are destination charges on behalf of the
   guide. Refunds run automatically (`reverse_transfer`), Stripe keeps its fee — so the platform carries it.

## Security and data protection

Implements level A + B of `12-datenschutz.md`:

- Every dashboard query is scoped to the logged-in guide; tests check that other guides get 404.
- Two-factor login (TOTP app, recovery codes, replay protection) — required for staff, and for guides unless
  `REQUIRE_GUIDE_2FA=false`. The admin uses the same login.
- Guest phone, emergency contact, experience and the TOTP secrets are encrypted in the database (Fernet,
  keys in `FIELD_ENCRYPTION_KEYS`, rotation supported).
- Access log: viewing a booking, the participant list with emergency contacts, exports and deletions.
- Exports and deleting guest data need the password (and code) again within 10 minutes.
- Guides can delete a guest's phone, emergency contact and experience on request; bookings and invoices stay.
- Guest pages: 256-bit secret tokens, rate-limited, closed a year after a finished tour, `Referrer-Policy:
  same-origin`. Gunicorn writes no access log (the tokens are in URLs); Cloud Run's request log still does.
- Strict CSP (no inline scripts), only the widget pages may be framed, templates auto-escape, emails are
  plain text, the public forms use a honeypot + signed timestamp + per-IP rate limit.
- iCal URLs: https only, private IPs rejected on every redirect hop, 5 MB limit.
- Stripe webhooks are signature-checked, idempotent and verify amount and currency.
