# Tourchef – Buchungen für Bergführer

Mountain guides embed a booking calendar on their website. Guests request a tour, the guide confirms
(or proposes another date/price), and only then does the guest pay — online via Stripe Connect,
or manually (bank transfer, cash).

Django 5.2 LTS · PostgreSQL 16 · server-rendered templates (no JS framework) · Stripe Connect Express.

Emails go out in German or English: guests choose in the widget (preselected from the browser or `data-lang` on the
embed script), guides in their profile. The dashboard, widget and guest page are German for now.

Open tasks before going live: [ToDo.md](ToDo.md). Pilot test with a guide: [TEST_PLAN.md](TEST_PLAN.md).

## Booking flow

```
Guest (widget)            Guide (dashboard)                 Guest (link in email)
──────────────            ─────────────────                 ─────────────────────
request ───────────────▶  requested
                          ├─ confirm ─────────────────────▶ awaiting_payment ── pays ──▶ paid
                          ├─ counter-offer ──▶ offered ───▶ accept ─┘ / decline ──▶ declined
                          └─ reject ──▶ rejected
Open bookings expire after their deadline (REQUEST_RESPONSE_DAYS / GUEST_RESPONSE_DAYS).
```

Requested, offered, awaiting-payment and paid bookings hold their days. A PostgreSQL exclusion
constraint makes double bookings impossible, even under concurrent requests.

## Project layout

| Path | What |
|---|---|
| `config/` | settings (all from env vars), URLs |
| `accounts/` | guide accounts: email login, invite-code signup, password reset |
| `bookings/models.py` | Tour, Booking, BookingEvent (audit log), BlockedPeriod, ExternalBusy |
| `bookings/availability.py` | which days are bookable (weekday rules, booking window, blocks, calendar, bookings) |
| `bookings/services.py` | booking state machine — every status change goes through here |
| `bookings/calsync.py` | iCal import (recurring events, time zones, SSRF-protected fetch) |
| `bookings/views_widget.py` | public embeddable widget `/w/<slug>/` |
| `bookings/views_guest.py` | guest page `/b/<secret-token>/` (accept offer, pay, cancel) |
| `bookings/views_dashboard.py` | guide dashboard `/dashboard/` |
| `payments/` | Stripe Connect onboarding, Checkout, webhook |
| `static/js/embed.js` | one-line embed script with auto-height |

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
```

```bash
python manage.py migrate
python manage.py createcachetable
python manage.py createsuperuser
python manage.py runserver
pytest
```

Use `127.0.0.1` rather than `localhost` in `DATABASE_URL` on Windows (IPv6 lookups can hang).
Without `EMAIL_HOST`, emails are printed to the console.

## Deployment

1. `cp .env.example .env` and fill in every value (`SECRET_KEY`, `POSTGRES_PASSWORD`, `SITE_URL`, SMTP, Stripe).
2. `docker compose up -d --build` — starts Postgres, the web app (gunicorn, port 8000 on localhost)
   and a scheduler that hourly expires overdue bookings and syncs guides' calendars.
3. Put a TLS reverse proxy (e.g. Caddy) in front of port 8000; set `NUM_PROXIES=1`.
4. `docker compose exec web python manage.py createsuperuser` — admin lives at `/$ADMIN_URL`.
5. Give guides the `GUIDE_SIGNUP_CODE`; they register at `/konto/registrieren/`.
6. Back up the `pgdata` volume (e.g. nightly `pg_dump`).

### Stripe

1. Enable Connect in the Stripe dashboard (Express accounts, Switzerland). Enable TWINT under
   Settings → Payment methods (also for connected accounts) if you want it.
2. Create a webhook to `<SITE_URL>/zahlungen/stripe/webhook/`:
   - "Your account": `checkout.session.completed`, `checkout.session.async_payment_succeeded` → `STRIPE_WEBHOOK_SECRET`
   - "Connected accounts": `account.updated` → `STRIPE_CONNECT_WEBHOOK_SECRET`
3. Guides click "Mit Stripe verbinden" in their settings. Payments are destination charges on behalf
   of the guide; the platform keeps `PLATFORM_FEE_PERCENT`.

Refunds are done in the Stripe dashboard.

## Security notes

- Every dashboard query is scoped to the logged-in guide; other guides' data returns 404.
- Templates auto-escape all output; emails are plain text; strict CSP (no inline scripts).
- Only the widget may be framed (optionally restricted to the guide's own sites); everything else sends `X-Frame-Options: DENY`.
- The widget form is CSRF-exempt by design (third-party iframe, no session); it's protected by a honeypot,
  a signed form timestamp and per-IP rate limiting. Login, signup and password reset are rate-limited too.
- Guest pages use unguessable 256-bit tokens and `Referrer-Policy: same-origin`.
- iCal URLs: https only, private/internal IPs rejected on every redirect hop, 5 MB limit.
- Stripe webhooks are signature-checked, idempotent, and verify amount and currency before marking a booking paid.
