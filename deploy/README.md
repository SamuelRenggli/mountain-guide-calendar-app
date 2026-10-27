# Deployment (Google Cloud Run)

Every push to `main` runs the tests and, if they pass, deploys to Cloud Run in Zürich
(`.github/workflows/deploy.yml`). Pull requests and other branches only run the tests.

```
git push (main) ─▶ GitHub Actions: tests ─▶ build image ─▶ migrate job ─▶ new app version
Cloud Scheduler (hourly) ─▶ job tourchef-hourly: expire bookings, sync calendars
```

| Part | Where | Cost |
|---|---|---|
| App | Cloud Run `tourchef`, scales to zero | free tier |
| Database | Neon Postgres (Frankfurt) | free plan |
| Config and secrets | Secret Manager `tourchef-env` (one file) | free tier |
| Images | Artifact Registry, keeps the 2 newest | free tier |
| Hourly jobs | Cloud Scheduler + Cloud Run job | free tier |

**Cost cap:** the budget "Tourchef" (10 CHF/month) emails the billing admins at 0.01, 1, 5, 9 and
10 CHF. At 10 CHF the function `budget-stop` ([budget-stop/](budget-stop/)) detaches billing from
the project, which stops everything in it. Cost data arrives with a few hours' delay, so the stop
happens at roughly 10 CHF. The database (Neon), code (GitHub) and config (`.env.cloudrun`) live
outside the project and are not affected.

To restart after a stop: re-link billing (`gcloud billing projects link tourchef-test-sr
--billing-account=...`), run `deploy/gcp-setup.sh` again, push to `main`.

## One-time setup

1. Log in: `gcloud auth login` and `gh auth login`.
2. Create the Google resources (Git Bash):
   ```bash
   PROJECT_ID=tourchef-<suffix> BILLING_ACCOUNT=<id from: gcloud billing accounts list> bash deploy/gcp-setup.sh
   ```
3. Set the GitHub repository variables the script prints (`gh variable set NAME --body VALUE`).
4. Copy `deploy/cloudrun.env.example` to `.env.cloudrun`, fill it in, upload:
   `bash deploy/push-config.sh`
5. Push to `main`. The first deploy takes ~5 minutes.
6. Register at `<SITE_URL>/konto/registrieren/` with `GUIDE_SIGNUP_CODE`, and give the code to the test guide.

## Daily use

- Deploy: push or merge to `main`. Watch it under the repo's Actions tab.
- Change config (SMTP, Stripe, signup code): edit `.env.cloudrun`, run `bash deploy/push-config.sh`.
- Logs: `gcloud run services logs read tourchef --region=europe-west6 --limit=50`
- Roll back: Cloud Console → Cloud Run → tourchef → Revisions → route traffic to an older one.
