"""Cost cap: detaches billing from the project once the month's cost reaches LIMIT.

Triggered by the budget's Pub/Sub notifications (several per day). Detaching billing stops every
paid service in the project. To restart: re-link billing, run deploy/gcp-setup.sh, push to main.
"""

import base64
import json
import os

import functions_framework
from googleapiclient import discovery

PROJECT_ID = os.environ["PROJECT_ID"]
LIMIT = float(os.environ.get("LIMIT", "10"))


@functions_framework.cloud_event
def stop_billing(cloud_event):
    data = json.loads(base64.b64decode(cloud_event.data["message"]["data"]))
    cost = float(data["costAmount"])
    currency = data.get("currencyCode", "")
    if cost < LIMIT:
        print(f"Cost {cost:.2f} {currency} is below the limit of {LIMIT:.2f}, nothing to do.")
        return

    billing = discovery.build("cloudbilling", "v1", cache_discovery=False)
    name = f"projects/{PROJECT_ID}"
    if not billing.projects().getBillingInfo(name=name).execute().get("billingEnabled"):
        print("Billing is already disabled.")
        return
    billing.projects().updateBillingInfo(name=name, body={"billingAccountName": ""}).execute()
    print(f"Cost {cost:.2f} {currency} reached the limit of {LIMIT:.2f}: billing disabled for {PROJECT_ID}.")
