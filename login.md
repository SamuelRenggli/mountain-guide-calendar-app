# Sign up and log in

How to get into the app locally: as a guide (your own calendar and tours) and as admin. Start the
server first, see [run_app.md](run_app.md).

If port 8000 is taken by another app, run `python manage.py runserver 8001` and use `8001` in the
URLs below.

## Guide account

Signup is invite-only. The invite code is `GUIDE_SIGNUP_CODE` in `.env`:

```powershell
Select-String GUIDE_SIGNUP_CODE .env
```

1. Open http://127.0.0.1:8000/konto/registrieren/
2. Fill in name, email, password (twice) and the code under **Einladungscode**.
3. After signup you are logged in and land on your tours page. Create your tours there.

If `GUIDE_SIGNUP_CODE` is empty, the page says signup is closed. Set a code in `.env` and restart
`runserver`.

Next time, log in with email and password at http://127.0.0.1:8000/konto/anmelden/

Dashboard: http://127.0.0.1:8000/dashboard/

## Admin

The admin path is `ADMIN_URL` in `.env`, so it is not at `/admin/`:

```powershell
Select-String ADMIN_URL .env
```

Open `http://127.0.0.1:8000/<ADMIN_URL>`. You need a superuser. Create one once (skip this if you
already did it in [initial_setup.md](initial_setup.md)):

```powershell
python manage.py createsuperuser
```

## Forgot password

http://127.0.0.1:8000/konto/passwort/vergessen/

Without `EMAIL_HOST` in `.env`, the reset email is printed in the `runserver` terminal. Copy the
link from there.

Or reset it directly:

```powershell
python manage.py changepassword <email>
```

## Too many attempts

Login is limited to 10 attempts per 15 minutes, signup to 5 per hour (per IP). Wait it out, or
clear the limit (it lives in the database cache, so restarting the server does not reset it):

```powershell
python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```
