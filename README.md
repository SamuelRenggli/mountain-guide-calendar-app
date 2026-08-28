# Tourchef – Bergführer Buchungs-Plattform

A booking platform for mountain guides. Guides register, connect their Google Calendar, set available tour dates, and guests book directly via an embeddable iframe widget — powered by **FastAPI**, **MySQL**, **Docker**, and **Stripe**.

---

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose)
- That's it.

---

## Setup

### 1. Configure secrets

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `MYSQL_ROOT_PASSWORD` — any strong password
- `MAIL_USERNAME` / `MAIL_PASSWORD` — ProtonMail app password (https://account.proton.me/u/0/security)
- `STRIPE_SECRET_KEY` — from https://dashboard.stripe.com/apikeys
- `STRIPE_WEBHOOK_SECRET` — from Stripe Dashboard → Webhooks

### 2. Start the stack

```bash
docker-compose up --build
```

Docker will:
1. Pull and start **MySQL 8** (waits until healthy)
2. Build the **Python 3.11** image and install all dependencies
3. Start **FastAPI** with hot-reload on port `8000`
4. Auto-sync all guides' Google Calendars on startup (then every 6 hours)

---

## Key URLs

| URL | Description |
|-----|-------------|
| `http://localhost:8000/docs` | Swagger UI — test every endpoint |
| `http://localhost:8000/widget/{id}` | Public booking calendar widget (iframe) |
| `http://localhost:8000/dashboard/{id}` | Guide dashboard (bookings, calendar, sync) |

---

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/guides/` | Register a new mountain guide |
| `GET` | `/guides/{id}` | Get a guide by ID |
| `PATCH` | `/guides/{id}` | Update guide (name, email, ical_url) |
| `POST` | `/guides/{id}/touren/` | Add a tour |
| `GET` | `/guides/{id}/touren/` | List tours |
| `POST` | `/guides/{id}/timeslots/` | Add a timeslot |
| `GET` | `/guides/{id}/timeslots/` | List timeslots |
| `PATCH` | `/timeslots/{id}/status` | Update slot status (frei/gebucht/anfrage/blockiert) |
| `DELETE` | `/timeslots/{id}` | Delete a timeslot |
| `POST` | `/guides/{id}/sync-calendar` | Sync Google Calendar (iCal URL) |
| `POST` | `/bookings/` | Submit a booking request |
| `GET` | `/bookings/` | List all bookings |
| `POST` | `/bookings/{id}/mark-paid` | Manually mark a booking as paid |
| `POST` | `/bookings/{id}/pay` | Create Stripe Checkout session |
| `POST` | `/stripe/webhook` | Stripe webhook (auto payment confirmation) |

---

## Embedding the Widget

Every guide gets a public calendar widget. To embed it on any website (WordPress, Wix, Webflow):

```html
<iframe src="https://your-domain.com/widget/1" width="100%" height="600px" frameborder="0"></iframe>
```

Replace `1` with the guide's ID.

---

## Google Calendar Sync

Each guide can connect their Google Calendar:
1. In Google Calendar → Settings → **"Secret address in iCal format"** → copy URL
2. Store it: `PATCH /guides/{id}` with `{ "ical_url": "https://..." }`
3. The server auto-syncs every 6 hours — or trigger manually via the Dashboard

---

## Stripe / TWINT Payments

Payments run via **Stripe Checkout** (supports credit card + TWINT for Swiss accounts).

For local testing, install the Stripe CLI and run:
```bash
stripe listen --api-key sk_test_... --forward-to localhost:8000/stripe/webhook
```
The printed `whsec_...` key goes into `.env` as `STRIPE_WEBHOOK_SECRET`.

---

## Project Structure

```
kalender/
├── docker-compose.yml       # MySQL + FastAPI stack
├── Dockerfile               # Python 3.11 image
├── requirements.txt         # Python dependencies
├── .env                     # Secrets — NEVER commit this
├── .env.example             # Template for .env — safe to commit
├── .copilotignore           # Keeps secrets out of Copilot context
├── .gitignore
└── app/
    ├── main.py              # All FastAPI routes
    ├── database.py          # SQLAlchemy engine & session
    ├── models.py            # DB tables: users, touren, timeslots, bookings
    ├── schemas.py           # Pydantic request/response models
    ├── crud.py              # DB operations
    ├── email.py             # SMTP email notifications
    └── templates/
        ├── widget.html      # Public booking calendar (iframe)
        └── dashboard.html   # Guide dashboard
```

---

## Database Schema

```
users       → mountain guides (name, email, ical_url, stripe_account_id)
touren      → tours offered by a guide (title, price, max_participants)
timeslots   → calendar dates (frei / gebucht / anfrage / blockiert)
bookings    → guest requests linked to a timeslot (payment_status: pending / paid)
```

---

## Timeslot Status Flow

```
frei → [guest books] → anfrage → [guide confirms] → gebucht
frei → [guide blocks / calendar sync] → blockiert
anfrage → [payment received] → gebucht (payment_status: paid)
```

---

## Useful Commands

```bash
# Start stack
docker-compose up --build

# Restart only the API (after .env changes)
docker-compose restart web

# View live logs
docker-compose logs -f web

# Stop everything
docker-compose down
```


---

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (includes Docker Compose)
- That's it.

---



### 2. Start the stack

```bash
docker-compose up --build
```

Docker will:
1. Pull and start **MySQL 8** (waits until the DB is healthy before continuing)
2. Build the **Python 3.11** image and install all dependencies
3. Start **FastAPI** in hot-reload mode on port `8000`

---

### 3. Open the interactive API docs

Once the stack is running, open your browser at:

```
http://localhost:8000/docs
```

You will see the full **Swagger UI** where you can test every endpoint directly in the browser.

---

## API Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/guides/` | Register a new mountain guide |
| `GET` | `/guides/{id}` | Get a guide by ID |
| `POST` | `/guides/{id}/touren/` | Add a tour for a guide |
| `GET` | `/guides/{id}/touren/` | List all tours of a guide |
| `POST` | `/guides/{id}/timeslots/` | Add an available date/slot |
| `GET` | `/guides/{id}/timeslots/` | List all slots of a guide |
| `POST` | `/bookings/` | Submit a booking request |
| `GET` | `/widget/{id}` | Public widget data (for iframe embed) |

---

## Embedding the Widget

Every guide gets a public widget endpoint. To embed it on any website (WordPress, Wix, Webflow, etc.), the guide pastes one line of HTML:

```html
<iframe src="https://your-domain.com/widget/1" width="100%" height="600px" frameborder="0"></iframe>
```

Replace `1` with the guide's actual ID from the database.

---

## Project Structure

```
kalender/
├── docker-compose.yml       # Starts MySQL + FastAPI together
├── Dockerfile               # Python image definition
├── requirements.txt         # Python dependencies
├── .env                     # Your secrets (never commit this!)
├── .env.example             # Template for .env
├── .gitignore
└── app/
    ├── main.py              # FastAPI routes (entry point)
    ├── database.py          # SQLAlchemy engine & session
    ├── models.py            # Database tables (users, touren, timeslots, bookings)
    ├── schemas.py           # Pydantic request/response models
    └── crud.py              # Create/Read/Update/Delete operations
```

---

## Database Schema

```
users          → mountain guides
touren         → tours offered by a guide
timeslots      → available dates on the calendar (frei / gebucht / anfrage / blockiert)
bookings       → guest booking requests linked to a timeslot
```

---

## Useful Commands

| Task | Command |
|------|---------|
| Start (first time or after code changes) | `docker-compose up --build` |
| Start (no rebuild needed) | `docker-compose up` |
| Stop | `Ctrl+C` then `docker-compose down` |
| Stop and delete database volume | `docker-compose down -v` |
| View live logs | `docker-compose logs -f web` |

---

## Next Steps

- [ ] Add Stripe Connect for payments and TWINT
- [ ] Build the HTML/JS calendar widget (served at `/widget/{id}`)
- [ ] Add a guide dashboard (manage tours, timeslots, bookings)
- [ ] Calendar sync via Cronofy or Cal.com
