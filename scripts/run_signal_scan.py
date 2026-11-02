"""Safe entry point for the scheduled production scanner.

Runs one market/timeframe per HTTP request and spaces requests to respect
the free Twelve Data limit of 8 credits per minute. It never places broker orders.
"""
import os
import sys
import time

import requests


def main():
    base_url = os.environ.get("SCANNER_API_URL", "").strip().rstrip("/")
    secret = os.environ.get("SIGNAL_CRON_SECRET", "").strip()
    if not base_url or not secret:
        print("Missing SCANNER_API_URL or SIGNAL_CRON_SECRET.", file=sys.stderr)
        return 2

    symbols = ["EUR/USD", "XAU/USD", "BTC/USD"]
    intervals = ["5min", "15min", "30min"]
    failures = 0
    session = requests.Session()
    headers = {"X-Scanner-Cron-Secret": secret}
    request_count = 0

    for interval in intervals:
        for symbol in symbols:
            # Keep each Twelve Data request at least 9 seconds apart so a
            # free-tier 8-credits/minute quota is not exceeded by this scan.
            if request_count:
                time.sleep(9)
            request_count += 1
            try:
                response = session.post(
                    base_url + "/api/push/scan",
                    params={"symbol": symbol, "interval": interval},
                    headers=headers,
                    timeout=(10, 30),
                )
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"response": response.text[:500]}
                print(f"{symbol} {interval} | HTTP {response.status_code} | {payload}", flush=True)
                if response.status_code != 200 or not isinstance(payload, dict) or payload.get("error"):
                    failures += 1
                elif any(check.get("status") == "analysis_error" for check in payload.get("checks", [])):
                    failures += 1
            except requests.RequestException as exc:
                print(f"{symbol} {interval} | ERROR | {exc}", file=sys.stderr, flush=True)
                failures += 1

    print(f"Scanner finished: 9 combinations, {failures} failures.", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
