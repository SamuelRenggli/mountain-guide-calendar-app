# Problems with the current setup

Stand 04.11.2026 · Setup: Google Cloud Run (Zürich) with a CHF 10/month cost stop, Neon Postgres free plan
(Frankfurt), Stripe Connect Express, no email provider configured yet.

Prices and free-plan limits below are from memory and change over time — check the providers' current pages
before relying on a number.

## 1. Blocks a real pilot right now

| Problem | Effect | Fix |
|---|---|---|
| `EMAIL_HOST` is empty in `.env.cloudrun` | No email is sent; they only appear in the Cloud Run log. Guests get no confirmations or links to their booking, guides get no notice of new requests, password reset doesn't work. | SMTP provider, e.g. Brevo (free: 300 emails/day). Sending address on your own domain with SPF, DKIM and DMARC, otherwise mails land in spam. |
| `STRIPE_SECRET_KEY` is empty | Nobody can pay online; guides can't publish scheduled dates (they require Stripe). | Stripe test keys first, then live. Add webhook events `checkout.session.expired` and `checkout.session.async_payment_failed`; turn on TWINT for connected accounts. |
| `OPERATOR_ADDRESS`, `PRIVACY_EMAIL` not set | Legal notice and privacy policy show placeholders. | Set both before the first real guest books, then `bash deploy/push-config.sh`. |

## 2. Money (Stripe)

- **Tourchef pays Stripe's fee on every payment, not only on refunds.** With destination charges the platform
  carries the processing fee, and `PLATFORM_FEE_PERCENT` is 0. Decision 33 only meant refund fees. At roughly 3 % a
  CHF 960 signup costs Tourchef about CHF 28.
- **Refunds don't return the fee.** Every cancelled scheduled date or guest cancellation costs Tourchef the fee of
  the original payment.
- **Connect Express account fees.** Stripe may charge the platform per active connected account per month and per
  payout.
- Options: set `PLATFORM_FEE_PERCENT` so it covers the fees, or switch to direct charges so the guide pays
  them (code change in `payments/stripe_service.py`).

## 3. The CHF 10 cost stop

- When it triggers, billing is detached and **everything stops**: widget, scheduled-date pages, guest pages,
  dashboard, Stripe webhooks and the hourly job.
- While it's off: undecided dates aren't auto-cancelled, reminders aren't sent, seat holds aren't released, failed
  refunds aren't retried.
- Stripe retries webhooks for about 3 days, so payments during a short outage are recorded once the app is back.
  A longer outage needs manual checking in the Stripe dashboard.
- Cost data arrives hours late, so the stop can come with little warning.
- Restart: re-link billing, run `deploy/gcp-setup.sh`, push to `main` (see `deploy/README.md`).

## 4. Database (Neon free plan)

- **Storage:** about 0.5 GB. Plenty for the pilot — a booking is a few KB, so tens of thousands fit.
- **Compute:** limited free compute hours per month. The hourly job wakes the database 24× a day; with normal
  traffic this should stay within the limit, but it's the first limit you'd reach as usage grows.
- **Backups:** the free plan only lets you restore a few hours to about a day back. A mistake noticed later can't
  be undone.
  - Fix: nightly `pg_dump` stored outside Neon (e.g. a Cloud Run job writing to a storage bucket, or a cron job on
    your own machine). Test a restore once.
  - The dump contains the encrypted fields; keep `FIELD_ENCRYPTION_KEYS` separately (password manager). Without
    the key a restored database has unreadable phone numbers, emergency contacts and 2FA secrets.

## 5. Cloud Run

- **Cold starts:** the app scales to zero. The first visit after a quiet period takes a few seconds (app and Neon
  both wake up), so a guest may briefly see an empty widget frame. `--min-instances=1` fixes it but costs money
  and would push against the cost stop.
- **Images:** Artifact Registry keeps the 2 newest images. With the new dependencies they may exceed the free
  0.5 GB slightly — a few cents per month.
- **Request logs** (kept about 30 days) contain the secret guest links in their URLs. The app itself no longer logs
  them; Cloud Run's request log can't be switched off per service.
- **Max 2 instances:** fine for the pilot; set by `--max-instances=2` in the deploy workflow.

## 6. Data that only grows

| Table | Grows with | Note |
|---|---|---|
| `django_session` | every login | Nothing clears expired sessions yet. Fix: add `python manage.py clearsessions` to `deploy/hourly.sh`. |
| `payments_stripeevent` | every webhook | Small; could be pruned after 90 days. |
| `accounts_accesslog` | every view of a booking or participant list | Small; decide how long to keep it (e.g. 1 year) and say so in the privacy policy. |
| `bookings_bookingevent` | every status change | Part of the booking history; keep. |

None of this matters for the pilot's size, but it adds up over years.

## 7. Not a problem yet, but later

- **Photo sharing** (planned): needs file storage (e.g. a Cloud Storage bucket). Photos are where storage costs and
  the cost stop really start to matter.
- **More guides after May 2027:** revisit the Neon plan (paid plan for longer backups), the cost cap, and a paid email
  plan.
