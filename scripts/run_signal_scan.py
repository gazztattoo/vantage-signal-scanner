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
    # M5 y M15 son automáticos. M30 queda reservado para análisis manual
    # desde el panel y nunca se incluye en el escáner programado.
    minute = started_at.minute
    due_intervals = []
    if minute % 20 < 10:
        due_intervals.append("5min")
    if minute % 30 < 10:
        due_intervals.append("15min")

    failures = []
    request_count = 0

    if not due_intervals:
        message = f"No scheduled intervals at UTC minute {minute:02d}; API was not called."
        print(message, flush=True)
        write_step_summary([
            "## Vantage Signal Scanner",
            f"- Started: {started_at.isoformat()}",
            "- Result: no interval due",
            "- API requests: 0",
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
            try:
                response = session.post(
                    base_url + "/api/push/scan",
                    params={"symbol": symbol, "interval": interval},
                    headers=headers,
                    timeout=(10, 45),
                )
                try:
                    payload = response.json()
                except ValueError:
                    payload = {"error": response.text[:300]}

                if response.status_code != 200 or not isinstance(payload, dict) or payload.get("error"):
                    failures.append(f"{symbol} {interval}: HTTP {response.status_code}")
                    detail = ""
                    if isinstance(payload, dict):
                        checks_detail = payload.get("checks")
                        if isinstance(checks_detail, list) and checks_detail:
                            detail = str(checks_detail[0].get("detail") or checks_detail[0].get("status") or "")
                        detail = detail or str(payload.get("error") or "")
                    print(
                        f"{symbol} {interval} | HTTP {response.status_code} | API error"
                        + (f" | detail={detail[:300]}" if detail else ""),
                        flush=True,
                    )
                    continue

                checks = payload.get("checks")
                if not isinstance(checks, list) or len(checks) != 1:
                    failures.append(f"{symbol} {interval}: expected exactly one check, got invalid/empty response")
                else:
                    check = checks[0]
                    status = check.get("status")
                    if status in FAILURE_STATUSES:
                        failures.append(
                            f"{symbol} {interval}: {status} "
                            f"({check.get('detail') or check.get('http_status') or 'sin detalle'})"
                        )
                    elif status not in SUCCESS_STATUSES:
                        failures.append(f"{symbol} {interval}: unrecognized check status {status!r}")

                queue = payload.get("delivery_queue") or {}
                if not isinstance(queue, dict) or int(queue.get("errors") or 0) > 0:
                    failures.append(f"{symbol} {interval}: push delivery queue errors")

                check_detail = ""
                if isinstance(checks, list) and checks:
                    check_detail = str(checks[0].get("detail") or checks[0].get("http_status") or "")
                print(
                    f"{symbol} {interval} | HTTP {response.status_code} | "
                    f"status={checks[0].get('status') if isinstance(checks, list) and checks else 'MISSING'}"
                    + (f" | detail={check_detail[:300]}" if check_detail else "")
                    + f" | queue={queue}",
                    flush=True,
                )
            except requests.RequestException as exc:
                message = f"{symbol} {interval}: request failed ({type(exc).__name__})"
                failures.append(message)
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
