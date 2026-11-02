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
    # Cadencia escalonada: M5 cada 20 min, M15 cada 30 min y M30 cada 60 min.
    # El workflow se ejecuta cada 10 min; los ciclos se alinean al reloj UTC.
    minute = time.gmtime().tm_min
    due_intervals = []
    if minute % 20 == 0:
        due_intervals.append("5min")
    if minute % 30 == 0:
        due_intervals.append("15min")
    if minute == 0:
        due_intervals.append("30min")

    failures = 0
    session = requests.Session()
    headers = {"X-Scanner-Cron-Secret": secret}
    request_count = 0

    for interval in due_intervals:
        for symbol in symbols:
            # Mantener al menos 9 segundos entre llamadas externas.
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

    print(f"Scanner finished: {request_count} scheduled combinations, {failures} failures.", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
