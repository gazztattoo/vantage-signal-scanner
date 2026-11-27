"""Read-only BiQuote feed diagnostics for TEST; never supplies trading data."""
from __future__ import annotations

from datetime import datetime, timezone
import requests

BASE = "https://biquote.io"
TIMEOUT = 6
INTERVALS = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
LIMITS = {"5m": 288, "15m": 192, "30m": 192, "1h": 168, "4h": 180}


def _get(path, *, params=None):
    checked = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.get(
            BASE + path, params=params, timeout=TIMEOUT,
            headers={"User-Agent": "Gedeon2-TEST-feed-diagnostics/1.0"},
        )
        result = {"http_status": response.status_code, "ok": response.ok, "checked_at": checked}
        if not response.ok:
            result["error"] = "provider_http_error"
            return result, None
        try:
            return result, response.json()
        except ValueError:
            result.update({"ok": False, "error": "invalid_json"})
            return result, None
    except requests.RequestException as exc:
        return {"ok": False, "checked_at": checked, "error": type(exc).__name__}, None


def _active_contains(payload, symbol):
    rows = payload
    if isinstance(payload, dict):
        for key in ("symbols", "active", "data", "items"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    if not isinstance(rows, list):
        return None
    for row in rows:
        value = row.get("symbol") or row.get("name") or row.get("ticker") if isinstance(row, dict) else row
        if str(value).replace("/", "").upper() == symbol:
            return True
    return False


def _last_bar(payload, interval):
    if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list) or not payload["bars"]:
        return {"bars_received": 0, "last_closed_open_time": None, "last_closed_age_minutes": None}
    bars = [b for b in payload["bars"] if isinstance(b, dict) and b.get("isOpen") is not True]
    if not bars:
        return {"bars_received": len(payload["bars"]), "closed_bars": 0, "last_closed_open_time": None, "last_closed_age_minutes": None}
    latest = max(bars, key=lambda b: str(b.get("openTime", "")))
    raw = latest.get("openTime")
    age = None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if stamp.tzinfo:
            age = round((datetime.now(timezone.utc) - (stamp.astimezone(timezone.utc).replace() + __import__("datetime").timedelta(minutes=INTERVALS[interval]))).total_seconds() / 60, 1)
    except (TypeError, ValueError):
        pass
    return {"bars_received": len(payload["bars"]), "closed_bars": len(bars), "last_closed_open_time": raw, "last_closed_age_minutes": age}


def diagnose_biquote():
    """Probe provider health, active symbols, quote, tick history and OHLC independently."""
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "provider": "biquote",
        "symbol": "XAU/USD",
        "provider_symbol": "XAUUSD",
        "research_only": True,
        "signals_activated": False,
        "execution_price": False,
    }
    health, _ = _get("/health")
    active, active_payload = _get("/api/active")
    tick, tick_payload = _get("/api/XAUUSD", params={"allowStale": "false"})
    history, history_payload = _get("/api/XAUUSD/history")
    report["health"] = health
    report["active_symbols"] = {
        **active,
        "xauusd_present": _active_contains(active_payload, "XAUUSD") if active_payload is not None else None,
    }
    report["tick"] = tick
    if isinstance(tick_payload, dict):
        report["tick"].update({
            "symbol": tick_payload.get("symbol"),
            "timestamp": tick_payload.get("timestamp"),
            "market_state": tick_payload.get("marketState"),
            "stale": tick_payload.get("stale"),
            "bid_ask_present": isinstance(tick_payload.get("bid"), (int, float)) and isinstance(tick_payload.get("ask"), (int, float)),
        })
    report["tick_history"] = history
    if isinstance(history_payload, dict):
        rows = history_payload.get("ticks") or history_payload.get("history") or history_payload.get("data")
        report["tick_history"]["records_received"] = len(rows) if isinstance(rows, list) else None
        report["tick_history"]["latest_timestamp"] = (
            (rows[-1].get("timestamp") if isinstance(rows[-1], dict) else None)
            if isinstance(rows, list) and rows else None
        )
    report["ohlc"] = {}
    for interval, limit in LIMITS.items():
        result, payload = _get("/api/XAUUSD/ohlc", params={"interval": interval, "limit": limit})
        if payload is not None:
            result.update(_last_bar(payload, interval))
        report["ohlc"][interval] = result
    return report
