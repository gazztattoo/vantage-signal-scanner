"""Safe entry point for the scheduled production scanner.

Runs one market/timeframe per HTTP request and spaces requests to respect
the external Biquote feed without consuming Twelve Data credits. It never places broker orders.
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
    "confluence_read_error",
}
SUCCESS_STATUSES = {
    "confluence_wait",
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
    # Fail closed: no hay consultas a la API si la ventana de prueba falta,
    # tiene formato inválido o ya terminó.
    start_raw = os.environ.get("SCANNER_TRIAL_START_UTC", "").strip()
    end_raw = os.environ.get("SCANNER_TRIAL_END_UTC", "").strip()
    try:
        trial_start = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        trial_end = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
        if trial_start.tzinfo is None or trial_end.tzinfo is None or trial_end <= trial_start:
            raise ValueError("trial timestamps must be timezone-aware and ordered")
    except ValueError:
        print("Invalid or missing scanner trial window; refusing to contact the API.", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    if now < trial_start.astimezone(timezone.utc):
        message = f"Trial has not started; no scan sent. Starts at {trial_start.astimezone(timezone.utc).isoformat()}."
        print(message, flush=True)
        write_step_summary(["## GLOBAL SIGNAL SCANNER", f"- {message}"])
        return 0
    if now >= trial_end.astimezone(timezone.utc):
        message = f"Trial ended; automatic stop active. No scan sent. Ended at {trial_end.astimezone(timezone.utc).isoformat()}."
        print(message, flush=True)
        write_step_summary(["## GLOBAL SIGNAL SCANNER", f"- {message}"])
        return 0

    base_url = os.environ.get("SCANNER_API_URL", "").strip().rstrip("/")
    secret = os.environ.get("SIGNAL_CRON_SECRET", "").strip()
    if not base_url or not secret:
        print("Missing SCANNER_API_URL or SIGNAL_CRON_SECRET.", file=sys.stderr)
        return 2

    # Oro primero: prioridad de atención sin aumentar frecuencia ni consumo de API.
    symbols = ["XAU/USD", "EUR/USD", "BTC/USD"]
    started_at = datetime.now(timezone.utc)
    # Ejecutar M5 y M15 en cada ciclo de GitHub Actions.
    # No dependemos del minuto exacto de inicio (GitHub puede retrasar
    # los cron): el backend usa Biquote y rechaza cotizaciones/velas antiguas.
    # No hay fallback a Twelve Data; TEST no utiliza proveedores live.
    # M30 queda reservado exclusivamente al análisis manual del panel.
    due_intervals = ["5min", "15min"]

    failures = []
    request_count = 0

    session = requests.Session()
    headers = {"X-Scanner-Cron-Secret": secret}

    for interval in due_intervals:
        for symbol in symbols:
            # Separar las llamadas para no sobrecargar el proveedor ni la API.
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
