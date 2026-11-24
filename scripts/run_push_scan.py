#!/usr/bin/env python3
"""Trigger one authenticated TEST-only BiQuote push scan and exit."""
import json
import os
import sys

import requests


def main():
    base_url = os.environ.get("SIGNAL_SCAN_API_URL", "").strip().rstrip("/")
    secret = os.environ.get("SIGNAL_CRON_SECRET", "")
    if not base_url or not secret:
        print("Missing SIGNAL_SCAN_API_URL or SIGNAL_CRON_SECRET", file=sys.stderr)
        return 2

    url = base_url + "/api/push/scan"
    try:
        response = requests.post(
            url,
            headers={"X-Scanner-Cron-Secret": secret},
            timeout=(10, 210),
        )
    except requests.RequestException as exc:
        print(f"Scan request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        payload = response.json()
    except ValueError:
        payload = {"response_excerpt": response.text[:500]}

    # Never print credentials or request headers.
    print(json.dumps({
        "http_status": response.status_code,
        "result": payload,
    }, ensure_ascii=False, default=str))

    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
