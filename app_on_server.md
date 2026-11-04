# App on the server

The app runs on Google Cloud Run in Zürich. Every push to `main` deploys automatically
(see [deploy/README.md](deploy/README.md)).

Base address: **https://tourchef-447564206646.europe-west6.run.app/**

| What | Address |
|---|---|
| Guide login | https://tourchef-447564206646.europe-west6.run.app/konto/anmelden/ |
| Guide signup (needs `GUIDE_SIGNUP_CODE` from `.env.cloudrun`) | https://tourchef-447564206646.europe-west6.run.app/konto/registrieren/ |
| Dashboard | https://tourchef-447564206646.europe-west6.run.app/dashboard/ |
| Widget of a guide | https://tourchef-447564206646.europe-west6.run.app/w/&lt;widget-id&gt;/ (widget ID: Dashboard → Einstellungen) |
| Privacy policy | https://tourchef-447564206646.europe-west6.run.app/rechtliches/datenschutz/ |
| Legal notice | https://tourchef-447564206646.europe-west6.run.app/rechtliches/impressum/ |
| Terms of use | https://tourchef-447564206646.europe-west6.run.app/rechtliches/nutzungsbedingungen/ |
| Health check | https://tourchef-447564206646.europe-west6.run.app/healthz |
| Admin | `<base address>/<ADMIN_URL>` — the path is in `.env.cloudrun` (kept out of this file on purpose) |

Scheduled dates have their own address `/t/<id>/`, shown on the date's page in the dashboard (Termine → date →
"Link zum Teilen").

## First login after the update

1. Log in with email and password.
2. You're asked to set up two-factor login: scan the QR code with an authenticator app (Google Authenticator,
   Microsoft Authenticator, 1Password) and enter the 6-digit code.
3. Write down the recovery codes — they are shown only once.

## Notes

- The first visit after a quiet period takes a few seconds (the app scales to zero).
- No emails go out yet and online payment isn't set up — see
  [Problems-with-the-current-setup.md](Problems-with-the-current-setup.md).
- Logs: `gcloud run services logs read tourchef --region=europe-west6 --limit=50`
