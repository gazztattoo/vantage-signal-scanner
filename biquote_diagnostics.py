"""Read-only BiQuote feed diagnostics for TEST; never supplies trading data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
            headers={"User-Agent": "Gedeon2-TEST-feed-diagnostics/1.1"},
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


def _as_rows(payload, keys=()):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    return None


def _symbol_name(row):
    if isinstance(row, dict):
        return row.get("name") or row.get("symbol") or row.get("ticker")
    return row if isinstance(row, str) else None


def _contains_symbol(payload, symbol, keys=()):
    rows = _as_rows(payload, keys)
    if rows is None:
        return None
    return any(str(_symbol_name(row) or "").replace("/", "").upper() == symbol for row in rows)


def _parse_timestamp(value):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _age_minutes(value):
    stamp = _parse_timestamp(value)
    if stamp is None:
        return None
    return round((datetime.now(timezone.utc) - stamp).total_seconds() / 60, 1)


def _last_bar(payload, interval):
    rows = _as_rows(payload, ("bars",))
    if not rows:
        return {
            "bars_received": len(rows) if rows is not None else 0,
            "closed_bars": 0,
            "last_closed_open_time": None,
            "last_closed_age_minutes": None,
            "freshness": "unknown",
        }
    bars = [bar for bar in rows if isinstance(bar, dict) and bar.get("isOpen") is not True]
    if not bars:
        return {
            "bars_received": len(rows), "closed_bars": 0,
            "last_closed_open_time": None, "last_closed_age_minutes": None,
            "freshness": "unknown",
        }
    latest = max(bars, key=lambda bar: _parse_timestamp(bar.get("openTime")) or datetime.min.replace(tzinfo=timezone.utc))
    raw = latest.get("openTime")
    age = _age_minutes(raw)
    # A closed candle's age is measured from its close boundary, not its open.
    if age is not None:
        age = round(age - INTERVALS[interval], 1)
    return {
        "bars_received": len(rows),
        "closed_bars": len(bars),
        "last_closed_open_time": raw,
        "last_closed_age_minutes": age,
        "freshness": "fresh" if age is not None and -1 <= age <= INTERVALS[interval] * 2 else "stale" if age is not None else "unknown",
    }


def _tick_history_summary(payload):
    rows = _as_rows(payload, ("ticks", "history", "data"))
    if rows is None:
        return {"records_received": None, "latest_timestamp": None, "latest_age_seconds": None, "freshness": "unknown"}
    # BiQuote documents historical ticks newest first.
    latest = rows[0] if rows and isinstance(rows[0], dict) else None
    timestamp = latest.get("timestamp") if latest else None
    age = _age_minutes(timestamp)
    age_seconds = round(age * 60, 1) if age is not None else None
    return {
        "records_received": len(rows),
        "latest_timestamp": timestamp,
        "latest_age_seconds": age_seconds,
        "freshness": "fresh" if age_seconds is not None and -60 <= age_seconds <= 300 else "stale" if age_seconds is not None else "unknown",
    }


def diagnose_biquote():
    """Probe only documented BiQuote endpoints; output is research-only."""
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
    catalog, catalog_payload = _get("/api/symbols/XAUUSD")
    search, search_payload = _get("/api/symbols/search", params={"q": "gold", "limit": 50})
    tick, tick_payload = _get("/api/XAUUSD", params={"allowStale": "false"})
    history, history_payload = _get("/api/XAUUSD/history", params={"count": 100})

    report["health"] = health
    report["active_symbols"] = {
        **active,
        "xauusd_present": _contains_symbol(active_payload, "XAUUSD", ("symbols", "active", "data", "items"))
            if active_payload is not None else None,
    }
    report["symbol_catalog"] = {
        **catalog,
        "xauusd_found": catalog_payload is not None,
        "name": catalog_payload.get("name") if isinstance(catalog_payload, dict) else None,
        "source": catalog_payload.get("source") if isinstance(catalog_payload, dict) else None,
        "is_active": catalog_payload.get("isActive") if isinstance(catalog_payload, dict) else None,
    }
    report["gold_search"] = {
        **search,
        "xauusd_found": _contains_symbol(search_payload, "XAUUSD", ("symbols", "items", "data"))
            if search_payload is not None else None,
    }

    report["tick"] = tick
    if isinstance(tick_payload, dict):
        timestamp = tick_payload.get("timestamp")
        report["tick"].update({
            "symbol": tick_payload.get("symbol"),
            "timestamp": timestamp,
            "age_seconds": round(_age_minutes(timestamp) * 60, 1) if _age_minutes(timestamp) is not None else None,
            "market_state": tick_payload.get("marketState"),
            "stale": tick_payload.get("stale"),
            "bid_ask_present": isinstance(tick_payload.get("bid"), (int, float)) and isinstance(tick_payload.get("ask"), (int, float)),
        })

    report["tick_history"] = {**history, **_tick_history_summary(history_payload)}
    report["ohlc"] = {}
    for interval, limit in LIMITS.items():
        result, payload = _get("/api/XAUUSD/ohlc", params={"interval": interval, "limit": limit})
        if payload is not None:
            result.update(_last_bar(payload, interval))
        report["ohlc"][interval] = result

    active_gold = report["active_symbols"].get("xauusd_present") is True
    tick_fresh = report["tick"].get("ok") is True and report["tick"].get("stale") is False
    report["feed_verdict"] = (
        "LIVE_TICK_AVAILABLE" if active_gold and tick_fresh
        else "SYMBOL_KNOWN_BUT_NOT_LIVE" if report["symbol_catalog"].get("xauusd_found")
        else "SYMBOL_NOT_CONFIRMED"
    )
    return report
