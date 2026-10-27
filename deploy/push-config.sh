#!/usr/bin/env bash
# Uploads the production config (.env.cloudrun, never committed) to Secret Manager and restarts the app.
#
#   bash deploy/push-config.sh
set -euo pipefail

FILE=${1:-.env.cloudrun}
REGION=${REGION:-europe-west6}
SERVICE=tourchef

NEW=$(gcloud secrets versions add tourchef-env --data-file="$FILE" --format='value(name)')
NEW=${NEW##*/}
# Only the newest version is needed; destroying the rest keeps Secret Manager in the free tier.
for v in $(gcloud secrets versions list tourchef-env --filter="state!=DESTROYED" --format='value(name)'); do
  v=${v##*/}
  [ "$v" = "$NEW" ] || gcloud secrets versions destroy "$v" --secret=tourchef-env --quiet >/dev/null
done
echo "Config version $NEW uploaded."

# A new revision picks up the new config (only once the app has been deployed).
if gcloud run services describe "$SERVICE" --region="$REGION" >/dev/null 2>&1; then
  gcloud run services update "$SERVICE" --region="$REGION" --update-labels="config=v$NEW" --quiet
fi
