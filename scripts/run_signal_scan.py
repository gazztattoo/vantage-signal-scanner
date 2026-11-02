"""Safe entry point for the scheduled production scanner.

Runs one market/timeframe per HTTP request and spaces requests to respect
the free Twelve Data limit of 8 credits per minute. It never places broker orders.
"""
import os
import sys
import time
from datetime import datetime, timezone

import requests


FAILURE_STATUSES = {
    "analysis_error",
    "history_save_error",
    "state_read_error",
    "state_save_error",
    "subscription_read_error",
    "delivery_queue_error",
    "outbox_error",
}
SUCCESS_STATUSES = {
    "market_closed",
    "same_signal_no_notification",
    "state_reset_no_notification",
    "delivery_queued",
    "new_signal_no_subscribers",
    "duplicate_state",
}


def write_step_summary(lines):
    """Publish a compact run report in the GitHub Actions job summary."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"Could not write GitHub step summary: {exc}", file=sys.stderr)


def main():
    base_url = os.environ.get("SCANNER_API_URL", "").strip().rstrip("/")
    secret = os.environ.get("SIGNAL_CRON_SECRET", "").strip()
    if not base_url or not secret:
        print("Missing SCANNER_API_URL or SIGNAL_CRON_SECRET.", file=sys.stderr)
        return 2

    symbols = ["EUR/USD", "XAU/USD", "BTC/USD"]
    started_at = datetime.now(timezone.utc)
    # GitHub Actions may start a few minutes after the scheduled minute.
    # Accept the first 10-minute window of each cycle without adding scan rounds:
    # M5 every 20 min, M15 every 30 min, M30 once per hour.
    minute = started_at.minute
    due_intervals = []
    if minute % 20 < 10:
        due_intervals.append("5min")
    if minute % 30 < 10:
        due_intervals.append("15min")
    if minute < 10:
        due_intervals.append("30min")

    report = []
    failures = []
    request_count = 0
    results = []

    if not due_intervals:
        message = f"No scheduled intervals at UTC minute {minute:02d}; API was not called."
        print(message, flush=True)
        write_step_summary([
            "## Vantage Signal Scanner",
            f"- Started: {started_at.isoformat()}",
            f"- Result: no interval due",
            f"- API requests: 0",
            f"- Detail: {message}",
        ])
        return 0

    session = requests.Session()
    headers = {"X-Scanner-Cron-Secret": secret}

    for interval in due_intervals:
        for symbol in symbols:
            # Keep at least 9 seconds between requests.
            if request_count:
                time.sleep(9)
            request_count += 1
            item = {"symbol": symbol, "interval": interval}
            try:
                response = session.post(
                    base_url + "/api/push/scan",
                    params={"symbol": symbol, "interval": interval},
                    headers=headers,
                    timeout=(10, 45),
                )
                item["http_status"] = response.status_code
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"error": response.text[:300]}
                item["payload"] = payload

                if response.status_code != 200 or not isinstance(payload, dict) or payload.get("error"):
                    failures.append(f"{symbol} {interval}: HTTP {response.status_code}")
                else:
                    checks = payload.get("checks", [])
                    if not isinstance(checks, list):
                        failures.append(f"{symbol} {interval}: invalid checks payload")
                    else:
                        for check in checks:
                            status = check.get("status")
                            if status in FAILURE_STATUSES:
                                failures.append(
                                    f"{symbol} {interval} / {check.get('symbol')} "
                                    f"{check.get('interval')}: {status}"
                                )
                            elif status not in SUCCESS_STATUSES:
                                failures.append(
                                    f"{symbol} {interval}: unrecognized check status {status!r}"
                                )
                    queue = payload.get("delivery_queue") or {}
                    if int(queue.get("errors") or 0) > 0:
                        failures.append(f"{symbol} {interval}: push delivery queue errors")

                results.append(item)
                print(
                    f"{symbol} {interval} | HTTP {response.status_code} | "
                    f"checks={len(payload.get('checks', [])) if isinstance(payload, dict) else 0} | "
                    f"queue={payload.get('delivery_queue', {}) if isinstance(payload, dict) else {}}",
                    flush=True,
                )
            except requests.RequestException as exc:
                message = f"{symbol} {interval}: request failed ({type(exc).__name__})"
                failures.append(message)
                item["error"] = message
                results.append(item)
                print(message, file=sys.stderr, flush=True)

    finished_at = datetime.now(timezone.utc)
    status = "FAILED" if failures else "OK"
    print(
        f"Scanner finished: {request_count} requests, "
        f"{len(failures)} detected failures. Status: {status}.",
        flush=True,
    )

    summary_lines = [
        "## Vantage Signal Scanner — producción",
        f"- Inicio UTC: {started_at.isoformat()}",
        f"- Fin UTC: {finished_at.isoformat()}",
        f"- Intervalos: {', '.join(due_intervals)}",
        f"- Combinaciones solicitadas: {request_count}",
        f"- Estado: **{status}**",
    ]
    if failures:
        summary_lines.append("- Incidencias:")
        summary_lines.extend(f"  - {failure}" for failure in failures)
    else:
        summary_lines.append("- Incidencias detectadas: ninguna")
    write_step_summary(summary_lines)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
