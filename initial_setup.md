# Initial setup (run once)

Do this once on a new machine. Afterwards use [run_app.md](run_app.md) every time you start the app.

Run each block in a PowerShell terminal. Docker Desktop must be running.

## 1. Go to the project folder

```powershell
cd N:\Tourchef\kalender
```

## 2. Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

If PowerShell blocks the activate script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 3. Check `.env`

`.env` already exists. For local development it needs at least:

```
DEBUG=true
DATABASE_URL=postgres://tourchef:<password>@127.0.0.1:5433/tourchef
GUIDE_SIGNUP_CODE=<any code>
```

Without `EMAIL_HOST`, emails are printed to the console instead of sent.

## 4. Create the PostgreSQL container

The password must match the one in `DATABASE_URL` in `.env`.

```powershell
docker run -d --name tourchef-pg -e POSTGRES_USER=tourchef -e POSTGRES_PASSWORD=<password-from-.env> -e POSTGRES_DB=tourchef -p 127.0.0.1:5433:5432 postgres:16-alpine
```

Check it is up:

```powershell
docker ps --filter name=tourchef-pg
```

## 5. Set up the database

```powershell
python manage.py migrate
python manage.py createcachetable
python manage.py createsuperuser
```

If `migrate` fails with `password authentication failed`, the container password differs from
`.env`. Fix it without losing data (put in the password from `.env`):

```powershell
docker exec tourchef-pg psql -U tourchef -d tourchef -c "ALTER USER tourchef PASSWORD '<password-from-.env>';"
```

Setup done. From now on: [run_app.md](run_app.md).

---

## Alternative: full stack with Docker Compose

Runs Postgres, the web app (gunicorn) and the scheduler together. Needs `POSTGRES_PASSWORD` and
`SECRET_KEY` set in `.env`. Migrations run automatically on start. First time only:

```powershell
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Daily start/stop is in [run_app.md](run_app.md).
