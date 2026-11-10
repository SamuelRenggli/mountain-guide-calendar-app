# ToDo

Everything that has to happen outside the code, in order. Tick items off as you go.
The pilot test with one guide is described in [TEST_PLAN.md](TEST_PLAN.md).

## 0. Before merging this rebuild to `main` (deploys automatically)

- [ ] Generate `FIELD_ENCRYPTION_KEYS` (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`),
      put it into `.env.cloudrun` **and** a password manager, run `bash deploy/push-config.sh`. Without it the new
      version doesn't start; if it's lost, phone numbers, emergency contacts and 2FA secrets are unreadable.
- [ ] Also set `OPERATOR_NAME`, `OPERATOR_ADDRESS`, `PRIVACY_EMAIL` (shown in legal notice and privacy policy).
- [ ] Stripe webhook: add `checkout.session.expired` and `checkout.session.async_payment_failed`; turn on TWINT
      for connected accounts (or set `STRIPE_PAYMENT_METHODS=card`).
- [ ] After deploy, every guide sets up two-factor login on first login (have recovery codes ready).
- [ ] Review the legal drafts in `templates/legal/` (12 files: privacy, legal notice, terms × DE/FR/IT/EN) and the
      guest terms template in `templates/widget/terms.html`; replace the `[Anbieter eintragen]` placeholders.
- [ ] Have a French and an Italian speaker skim the translations (`locale/fr`, `locale/it`).

## 1. Right now (security)

- [ ] **Rotate the Stripe test keys.** The first commit (`57dfcad`) contained them in the docs, and they are still in
      the git history. Stripe dashboard → Developers → API keys → roll the secret key. Put the new key into `.env`.
- [ ] Delete the old webhook endpoint in Stripe (it pointed to the old `/stripe/webhook` URL) — new ones are set up in step 3.
- [ ] Check that the email app password in `.env` was never committed or shared. If unsure, create a new one.
- [ ] Before pushing this repo anywhere (GitHub etc.): don't push the old history. Either rewrite it
      (`git filter-repo`) or start a fresh repository from the `v2-django` branch.
- [ ] Set your git identity — commits currently use the placeholder `Your Name`:
      `git config --global user.name "…"` (optionally fix the last commit with `git commit --amend --reset-author`).
- [ ] Delete `.env.old-poc` once you've confirmed nothing in it is still needed.

## 2. Pilot server

- [ ] Pick hosting: a small VPS with Docker (2 GB RAM is enough), ideally in Switzerland/EU
      (e.g. Infomaniak, Exoscale, Hetzner).
- [ ] Domain/subdomain, e.g. `buchen.<deine-domain>.ch`, with an A record pointing to the server.
- [ ] TLS reverse proxy in front of the app. Simplest is Caddy (automatic Let's Encrypt), `Caddyfile`:
      ```
      buchen.example.ch {
          reverse_proxy 127.0.0.1:8000
      }
      ```
- [ ] Copy the repo to the server, create `.env` from `.env.example` (**pilot server .env**):
  - `DEBUG=false`
  - `SECRET_KEY` — new random value (`python -c "import secrets; print(secrets.token_urlsafe(50))"`), not the local one
  - `SITE_URL=https://buchen.example.ch`, `ALLOWED_HOSTS=buchen.example.ch`, `NUM_PROXIES=1`
  - `POSTGRES_PASSWORD` — new random value; remove `DATABASE_URL` (compose sets it)
  - `ADMIN_URL`, `GUIDE_SIGNUP_CODE` — new hard-to-guess values
  - `ADMIN_EMAILS` — your address (receives server errors and payment alerts)
  - SMTP and Stripe values (see below)
- [ ] `docker compose up -d --build`
- [ ] `docker compose exec web python manage.py createsuperuser` (your own admin account)
- [ ] Open `https://buchen.example.ch/healthz` → `ok`; log into `/<ADMIN_URL>`.
- [ ] Uptime monitoring on `/healthz` (e.g. UptimeRobot, free).
- [ ] Nightly database backup, kept off the server, e.g. a cron job:
      `docker compose exec -T db pg_dump -U tourchef tourchef | gzip > /backup/tourchef-$(date +%F).sql.gz`
- [ ] **Test a restore once** into a scratch database before real guests book.

## 3. Email

- [ ] Sending address on your own domain (e.g. `buchungen@<deine-domain>.ch`). Check that your provider allows SMTP
      (Proton Mail needs a paid plan with an SMTP token).
- [ ] SPF, DKIM and DMARC DNS records for that domain — otherwise booking emails land in spam.
- [ ] Send a test: register a test guide and use "Passwort vergessen" → email arrives, not in spam (Gmail + Outlook).

## 4. Stripe (start in TEST mode)

- [x] Stripe account for the platform; Connect enabled (Express accounts, country Switzerland). (Sandbox, 04.11.2026)
- [x] Accounts v1 support enabled (Settings → Developers → API policies). Stripe blocks v1 account creation for new
      Connect platforms by default; without it "Mit Stripe verbinden" fails.
- [ ] Before going live: enable Accounts v1 support in live mode too, or move account creation in
      `payments/stripe_service.py` (`onboarding_url`) to Accounts v2 (`POST /v2/core/accounts`).
- [ ] Connect settings: platform name, logo, support email (guides see this during onboarding).
- [x] Payment methods: cards and TWINT on (also for connected accounts) — scheduled dates accept only these.
- [x] Webhook endpoint `https://buchen.example.ch/zahlungen/stripe/webhook/`, events from **your account**:
      `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.expired`,
      `checkout.session.async_payment_failed` → secret into `STRIPE_WEBHOOK_SECRET`.
- [x] Second endpoint, same URL, events from **connected accounts**: `account.updated`
      → secret into `STRIPE_CONNECT_WEBHOOK_SECRET`.
- [ ] Decide the platform fee (`PLATFORM_FEE_PERCENT`, currently 0).
- [ ] After the pilot test passes: switch to live keys and create the two webhooks again in live mode.

## 5. Legal (Switzerland, nDSG; GDPR for EU guests)

- [x] Drafts of privacy policy, legal notice and terms of use (incl. data processing agreement) in four languages,
      linked everywhere; guests accept the guide's terms with every request and sign-up.
- [x] Cancellation template (14 days 0 %, 7 days 50 %, then 100 %), adjustable per guide; own guest terms possible.
- [x] Retention: phone, emergency contact, experience kept without limit, deleted on request (decision 42).
- [ ] Check the drafts against the SBV terms DOM brings (decision 31); legal review before expanding after May 2027.
- [ ] Short incident plan (who notices a leak, who decides, report to EDÖB and guides) — 12-datenschutz.md.

## 6. Pilot with one guide

- [ ] Run your own walkthrough first (TEST_PLAN.md, phase 1).
- [ ] Session with the guide (TEST_PLAN.md, phase 2): registration, tours, availability, calendar, Stripe, widget.
- [ ] Embed the widget first on a hidden test page of the guide's website, then on the real page.
- [ ] 2–4 weeks pilot with real guests (TEST_PLAN.md, phase 3); collect feedback.

## 7. After the pilot, before more guides

- [ ] Fix what the pilot found.
- [x] Widget, guest page, dashboard and emails in DE/FR/IT/EN.
- [x] CI runs `pytest` with a Postgres service on every push.
- [ ] Decide on `SECURE_HSTS_INCLUDE_SUBDOMAINS` (only if every subdomain is HTTPS).
- [x] Automatic refunds on cancellation.
- [ ] Optional: iCal export of bookings for guides.
