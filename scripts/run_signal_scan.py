"""Safe entry point for a Render Cron job.

This script only calls the scanner API. It does not place broker orders.
Configure SCANNER_API_URL and SIGNAL_CRON_SECRET in the Cron service environment.
"""
import os
import sys

import requests


def main():
    base_url = os.environ.get("SCANNER_API_URL", "").strip().rstrip("/")
    secret = os.environ.get("SIGNAL_CRON_SECRET", "").strip()
    if not base_url or not secret:
        print("Missing SCANNER_API_URL or SIGNAL_CRON_SECRET.", file=sys.stderr)
        return 2

    try:
        response = requests.post(
            base_url + "/api/push/scan",
            headers={"X-Scanner-Cron-Secret": secret},
            timeout=(10, 180),
        )
    except requests.RequestException as exc:
        print("Scanner request failed: " + str(exc), file=sys.stderr)
        return 1

    print("Scanner HTTP status:", response.status_code)
    try:
        payload = response.json()
    except ValueError:
        payload = {"response": response.text[:1000]}
    print(payload)

    if response.status_code != 200:
        return 1
    if not isinstance(payload, dict) or payload.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
