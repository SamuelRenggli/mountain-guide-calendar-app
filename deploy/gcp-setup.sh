#!/usr/bin/env bash
# One-time Google Cloud setup for Tourchef. Safe to run again: existing resources are kept.
#
#   PROJECT_ID=tourchef-xyz BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX bash deploy/gcp-setup.sh
#
# Creates: project, Artifact Registry (keeps the 2 newest images), service accounts, the config
# secret, keyless deploy access for GitHub Actions (main branch only), the hourly scheduler and
# a budget alert. The app itself is deployed by .github/workflows/deploy.yml.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${BILLING_ACCOUNT:?set BILLING_ACCOUNT}"
REGION=${REGION:-europe-west6}
GITHUB_REPO=${GITHUB_REPO:-SamuelRenggli/mountain-guide-calendar-app}
SERVICE=tourchef

exists() { "$@" >/dev/null 2>&1; }

echo "== Project"
exists gcloud projects describe "$PROJECT_ID" || gcloud projects create "$PROJECT_ID" --name=Tourchef
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" >/dev/null
gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

echo "== APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com \
  billingbudgets.googleapis.com

echo "== Artifact Registry"
exists gcloud artifacts repositories describe "$SERVICE" --location="$REGION" ||
  gcloud artifacts repositories create "$SERVICE" --repository-format=docker --location="$REGION"
gcloud artifacts repositories set-cleanup-policies "$SERVICE" --location="$REGION" \
  --policy=deploy/ar-cleanup.json --no-dry-run >/dev/null

echo "== Service accounts"
sa() { echo "$1@$PROJECT_ID.iam.gserviceaccount.com"; }
for name in tourchef-run tourchef-deploy tourchef-scheduler; do
  exists gcloud iam service-accounts describe "$(sa $name)" || gcloud iam service-accounts create "$name"
done
RUN_SA=$(sa tourchef-run)
DEPLOY_SA=$(sa tourchef-deploy)
SCHEDULER_SA=$(sa tourchef-scheduler)

bind_project() {
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$1" --role="$2" --condition=None >/dev/null
}
bind_project "$DEPLOY_SA" roles/run.admin
bind_project "$DEPLOY_SA" roles/artifactregistry.writer
bind_project "$SCHEDULER_SA" roles/run.invoker
gcloud iam service-accounts add-iam-policy-binding "$RUN_SA" \
  --member="serviceAccount:$DEPLOY_SA" --role=roles/iam.serviceAccountUser >/dev/null

echo "== Config secret"
exists gcloud secrets describe tourchef-env ||
  gcloud secrets create tourchef-env --replication-policy=user-managed --locations="$REGION"
gcloud secrets add-iam-policy-binding tourchef-env \
  --member="serviceAccount:$RUN_SA" --role=roles/secretmanager.secretAccessor >/dev/null

echo "== GitHub Actions access (keyless, main branch of $GITHUB_REPO only)"
exists gcloud iam workload-identity-pools describe github --location=global ||
  gcloud iam workload-identity-pools create github --location=global --display-name=GitHub
exists gcloud iam workload-identity-pools providers describe github --location=global --workload-identity-pool=github ||
  gcloud iam workload-identity-pools providers create-oidc github --location=global --workload-identity-pool=github \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='$GITHUB_REPO' && assertion.ref=='refs/heads/main'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GITHUB_REPO" >/dev/null

echo "== Hourly jobs (calendar sync, expiry)"
exists gcloud scheduler jobs describe "$SERVICE-hourly" --location="$REGION" ||
  gcloud scheduler jobs create http "$SERVICE-hourly" --location="$REGION" \
    --schedule="0 * * * *" --time-zone=Europe/Zurich --http-method=POST \
    --uri="https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/$SERVICE-hourly:run" \
    --oauth-service-account-email="$SCHEDULER_SA"

echo "== Budget alert"
CURRENCY=$(gcloud billing accounts describe "$BILLING_ACCOUNT" --format='value(currencyCode)')
if ! gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" --format='value(displayName)' | grep -qx Tourchef; then
  gcloud billing budgets create --billing-account="$BILLING_ACCOUNT" --display-name=Tourchef \
    --budget-amount="1${CURRENCY}" --filter-projects="projects/$PROJECT_ID" \
    --threshold-rule=percent=0.01 --threshold-rule=percent=1.0
fi

cat <<EOF

Done. App address: https://$SERVICE-$PROJECT_NUMBER.$REGION.run.app

GitHub repository variables (Settings -> Secrets and variables -> Actions -> Variables):
  GCP_PROJECT_ID    = $PROJECT_ID
  GCP_REGION        = $REGION
  GCP_WIF_PROVIDER  = projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github
  GCP_DEPLOY_SA     = $DEPLOY_SA
  GCP_RUN_SA        = $RUN_SA
EOF
