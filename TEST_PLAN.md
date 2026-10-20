# Test Plan – Pilot with one guide

**Goal:** prove that one real guide can run their bookings through Tourchef end to end — from a guest's
request on the guide's website to a paid, confirmed tour — without errors, lost emails or wrong money flows.

**Roles:** *Owner* (you) · *Guide* (the pilot guide) · *Guest* (you or friends, later real guests).

**What you need:** pilot server running (ToDo.md steps 2–4), Stripe in **test mode**, two email inboxes you
control (one set up to receive English emails), a phone and a laptop.

Record results in the tables (✅ / ❌ + note). Every ❌ gets a bug entry (template at the end).

---

## Phase 0 – Automated tests (owner, before every deployment)

```bash
pytest
```

112 tests cover availability, the booking state machine, double-booking prevention, access control between
guides, XSS escaping, widget spam protection, calendar parsing, SSRF protection, Stripe checkout and webhooks,
and all emails in German and English. **All must pass.**

## Phase 1 – Owner walkthrough on the pilot server (Stripe test mode, ~2 h)

Create a test guide yourself with the signup code, then go through every case below.

### Guide account

| ID | Steps | Expected | Result |
|---|---|---|---|
| G1 | Register at `/konto/registrieren/` with a wrong code | "Ungültiger Einladungscode", no account | |
| G2 | Register with the right code | Logged in, lands on "Touren" | |
| G3 | Log out, log in again | Dashboard shown | |
| G4 | 11 wrong passwords in a row | Locked out for 15 min ("Zu viele Anmeldeversuche") | |
| G5 | "Passwort vergessen" → link in email → new password | Can log in with the new password | |
| G6 | Settings → language "English" → password reset again | Reset email in English | |

### Tours & availability

| ID | Steps | Expected | Result |
|---|---|---|---|
| T1 | Create a 1-day tour (CHF 600, per group, max 4) and a 3-day tour (CHF 400, per person) | Both listed | |
| T2 | Deactivate one tour | It disappears from the widget | |
| A1 | Availability: untick Sunday | Sundays are not clickable in the widget | |
| A2 | Min. notice 3 days | Today+0..2 not clickable | |
| A3 | Block a period (e.g. a week next month) | Those days not clickable; shown as "Gesperrt" in Kalender | |
| A4 | 3-day tour: block one day | Start days whose 3 days include the blocked day are not clickable | |

### Calendar sync (Google)

| ID | Steps | Expected | Result |
|---|---|---|---|
| C1 | Paste the Google "secret address in iCal format" → save | "Kalender abgeglichen: N belegte Zeiträume" | |
| C2 | Google: all-day event next week | Day blocked after "Jetzt abgleichen" | |
| C3 | Google: timed event 09:00–17:00 | That day blocked | |
| C4 | Google: weekly recurring event | Every occurrence blocked | |
| C5 | Google: event marked "Free" (Verfügbar) | Day stays bookable | |
| C6 | Delete the event in Google, sync | Day bookable again | |
| C7 | Enter `http://…` or a nonsense URL | Clear error message, nothing breaks | |
| C8 | Wait > 1 h without clicking sync | "Zuletzt abgeglichen" time updated by the scheduler | |

### Widget

| ID | Steps | Expected | Result |
|---|---|---|---|
| W1 | Settings → copy script code → paste in a plain HTML test page (or the guide's hidden test page) | Calendar shown, height adapts, no scrollbar inside | |
| W2 | Same with the iframe code | Calendar shown | |
| W3 | Settings → "Erlaubte Websites" = another domain | Widget refuses to show on the test page | |
| W4 | Open the widget on a phone | Usable, days tappable, form readable | |
| W5 | Browser in English (or `data-lang="en"`) | Email language preselected "English" | |

### Booking flow (the core)

Use guest inbox 1 (German) unless stated.

| ID | Steps | Expected | Result |
|---|---|---|---|
| B1 | Widget: pick a day, fill in the form, send | Success page; guest gets "Deine Anfrage bei …"; guide gets "Neue Buchungsanfrage" with all details | |
| B2 | Widget again: same day | Day is no longer clickable | |
| B3 | Dashboard → request → **Bestätigen** with a message | Status "Bestätigt – Zahlung offen"; guest email with the pay link and the message | |
| B4 | Guest link → **jetzt bezahlen** → test card `4242 4242 4242 4242` | Back on the guest page; within seconds status "Bezahlt"; both get "Zahlung erhalten" | |
| B5 | Stripe dashboard → payment | Amount correct, transferred to the guide's connected account, platform fee as configured | |
| B6 | New request → guide sends **Gegenangebot** (other date, other price) | Guest email with new date/price; old day free again, new day blocked | |
| B7 | Guest accepts the offer | Guest gets confirmation + pay link; guide gets "Gegenangebot angenommen" | |
| B8 | Pay with declined card `4000 0000 0000 9995` | Stripe shows the error; booking stays "Zahlung offen" | |
| B9 | New request → guest declines an offer | Guide informed; days free | |
| B10 | New request → guide **Ablehnen** with a reason | Guest gets the reason; day free | |
| B11 | New request → guest "Anfrage zurückziehen" on the guest page | Guide informed; day free | |
| B12 | Paid booking → guide **Tour absagen** | Guest informed; refund done manually in Stripe | |
| B13 | Guide without Stripe connected: confirm a request → **Zahlung erhalten** | Guest email says the guide sends payment details; after marking, status "Bezahlt" | |
| B14 | Request from guest inbox 2 with language "English" | All guest emails in English, guide emails in the guide's language | |
| B15 | Pay link opened twice, two tabs, pay in both | Only one payment possible (the first Checkout page is expired) | |

### Deadlines

| ID | Steps | Expected | Result |
|---|---|---|---|
| X1 | Admin → booking (requested) → set "deadline" to yesterday → `docker compose exec web python manage.py expire_bookings` | Status "Abgelaufen"; both get an email; day free | |
| X2 | Same for a confirmed, unpaid booking (Stripe guide) | Expires the same way | |

### Security spot checks

| ID | Steps | Expected | Result |
|---|---|---|---|
| S1 | Second test guide: open `/dashboard/buchungen/<id of guide 1>/` | "Not found" | |
| S2 | Guest page with a changed token | "Not found" | |
| S3 | Request with name `<b>Test</b>` | Shown literally as text in the dashboard, not bold | |
| S4 | Embed the dashboard URL in an iframe on a test page | Browser refuses | |
| S5 | 6 requests within 10 minutes from the same network | 6th is refused ("Zu viele Anfragen") | |

### Operations

| ID | Steps | Expected | Result |
|---|---|---|---|
| O1 | `docker compose restart` | Everything back, data intact | |
| O2 | Restore last night's backup into a scratch database | Bookings present | |
| O3 | Trigger an error (e.g. stop the db briefly, load a page) | Error email to `ADMIN_EMAILS` | |

**Exit criterion phase 1:** all cases ✅, or ❌ only for cosmetic issues that are written down.

## Phase 2 – Session with the guide (~90 min, together)

Let the guide do the clicking; you watch and take notes without helping unless they're stuck.

1. Guide registers with the signup code (G2) and sets name, phone, language.
2. Guide creates their real tours (T1) and availability (A1–A3).
3. Guide connects their Google Calendar (C1), you check that private appointments are blocked.
4. Guide connects Stripe (Express onboarding — needs ID and bank account; **test mode** first).
5. You act as guest from your phone on the test page; guide handles the request: confirm (B3), offer (B6), reject (B10).
6. Guide embeds the widget on a hidden page of their website (W1).

Questions to ask afterwards:
- What was unclear? Where did you hesitate?
- Are the emails understandable — for you and for your guests?
- Is anything missing to run a real season with it (e.g. group sizes, deposits, cancellation rules)?
- How do guests usually pay you today?

**Exit criterion phase 2:** the guide completed steps 1–6 alone and wants to continue.

## Phase 3 – Live pilot (2–4 weeks, real guests)

Before: Stripe live keys + live webhooks (ToDo.md step 4), privacy policy linked, backup verified.

- Widget on the guide's real page.
- Owner checks daily: admin → Buchungen; `ADMIN_EMAILS` inbox; `docker compose logs --since 24h web scheduler` for errors.
- Weekly 15-min call with the guide.
- Track: number of requests, confirmed, paid, expired; emails reported as spam; any guest complaint.

**Go / no-go for more guides:**
- ≥ 3 real bookings paid without manual fixes in the database
- no lost or wrong payment, no double booking
- no guest email in spam reported more than once
- guide says they'd keep using it

## Bug report template

```
ID / title:
Test case:            (e.g. B6)
Device / browser:
Steps:
Expected:
Actual:
Screenshot / time:    (time helps finding it in the logs)
Severity:             blocker / major / minor / cosmetic
```
