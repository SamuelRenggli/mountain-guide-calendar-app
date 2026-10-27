# Run the app

Use this every time you open, test or develop the app. First time on a machine? Do
[initial_setup.md](initial_setup.md) first.

Docker Desktop must be running.

## 1. Start

```powershell
cd N:\Tourchef\kalender
.\.venv\Scripts\Activate.ps1
docker start tourchef-pg
python manage.py migrate
python manage.py runserver
```

`migrate` is a no-op when there is nothing new; it picks up migrations after a pull.

Open:

- App: http://127.0.0.1:8000/
- Guide signup: http://127.0.0.1:8000/konto/registrieren/ (needs `GUIDE_SIGNUP_CODE` from `.env`)
- Dashboard: http://127.0.0.1:8000/dashboard/
- Admin: http://127.0.0.1:8000/<ADMIN_URL from .env>

How to sign up and log in: [login.md](login.md).

Stop the server with `Ctrl+C`.

## 2. Run the tests (second terminal)

```powershell
cd N:\Tourchef\kalender
.\.venv\Scripts\Activate.ps1
pytest
```

## 3. Background jobs (optional, second terminal)

In production a scheduler runs these hourly. Locally, run them by hand when needed:

```powershell
python manage.py expire_bookings
python manage.py sync_calendars
```

## 4. Stop everything

```powershell
docker stop tourchef-pg
deactivate
```

If `requirements-dev.txt` changed after a pull, run `pip install -r requirements-dev.txt` once.

---

## Alternative: full stack with Docker Compose

Stop `runserver` first (both use port 8000).

```powershell
docker compose up -d --build
```

App at http://127.0.0.1:8000/. Logs and stop:

```powershell
docker compose logs -f web
```

```powershell
docker compose down
```
