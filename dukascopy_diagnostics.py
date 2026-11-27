"""Read-only Dukascopy diagnostics for the TEST branch.

This module ports only the public data decoding needed by dukascopy-node.
It never supplies prices or candles to the trading engine.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

import requests

API_ROOT = "https://jetta.dukascopy.com/v1"
REFERER = "https://widgets.dukascopy.com/en/historical-data-export"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/155.0.0.0 Safari/537.36"
)
SYMBOL_CODE = "XAU-USD"
MAX_TICK_AGE_SECONDS = 60


class DukascopyProbeError(RuntimeError):
    pass


def _get_json(path: str, from_ms: int) -> dict[str, Any]:
    response = requests.get(
        API_ROOT + path,
        params={"from": str(from_ms)},
        headers={
            "Referer": REFERER,
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise DukascopyProbeError("dukascopy_http_" + str(response.status_code))
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" not in content_type:
        raise DukascopyProbeError("dukascopy_non_json_response")
    try:
        payload = response.json()
    except ValueError as exc:
        raise DukascopyProbeError("dukascopy_invalid_json") from exc
    if not isinstance(payload, dict):
        raise DukascopyProbeError("dukascopy_invalid_payload")
    return payload


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _iso_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def _decode_ticks(data: dict[str, Any]) -> list[dict[str, Any]]:
    names = ("times", "asks", "bids")
    if any(not isinstance(data.get(name), list) for name in names):
        raise DukascopyProbeError("dukascopy_tick_columns_missing")
    times, asks, bids = (data[name] for name in names)
    if not (len(times) == len(asks) == len(bids)):
        raise DukascopyProbeError("dukascopy_tick_column_length_mismatch")
    if not times:
        return []
    multiplier = data.get("multiplier")
    timestamp = data.get("timestamp")
    ask = data.get("ask")
    bid = data.get("bid")
    if not all(_finite_number(v) for v in (multiplier, timestamp, ask, bid)):
        raise DukascopyProbeError("dukascopy_tick_base_invalid")
    if float(multiplier) <= 0:
        raise DukascopyProbeError("dukascopy_tick_multiplier_invalid")
    multiplier = float(multiplier)
    scale = max(0, len(str(multiplier).lower().split("e")[0].split(".")[-1]) -
                (int(str(multiplier).lower().split("e")[1]) if "e" in str(multiplier).lower() else 0))
    stamp = int(timestamp)
    ask_units = round(float(ask) / multiplier)
    bid_units = round(float(bid) / multiplier)
    result = []
    for delta, ask_delta, bid_delta in zip(times, asks, bids):
        if not all(_finite_number(v) for v in (delta, ask_delta, bid_delta)) or float(delta) < 0:
            raise DukascopyProbeError("dukascopy_tick_delta_invalid")
        stamp += int(delta)
        ask_units += int(ask_delta)
        bid_units += int(bid_delta)
        ask_price = round(ask_units * multiplier, scale)
        bid_price = round(bid_units * multiplier, scale)
        result.append({
            "timestamp_ms": stamp,
            "timestamp": _iso_ms(stamp),
            "ask": ask_price,
            "bid": bid_price,
        })
    return result


def _decode_minutes(data: dict[str, Any]) -> list[dict[str, Any]]:
    columns = ("times", "opens", "highs", "lows", "closes", "volumes")
    if any(not isinstance(data.get(name), list) for name in columns):
        raise DukascopyProbeError("dukascopy_candle_columns_missing")
    times, opens, highs, lows, closes, volumes = (data[name] for name in columns)
    size = len(times)
    if not all(len(column) == size for column in (opens, highs, lows, closes, volumes)):
        raise DukascopyProbeError("dukascopy_candle_column_length_mismatch")
    if not size:
        return []
    base_names = ("timestamp", "multiplier", "shift", "open", "high", "low", "close")
    if any(not _finite_number(data.get(name)) for name in base_names):
        raise DukascopyProbeError("dukascopy_candle_base_invalid")
    multiplier = float(data["multiplier"])
    shift = int(data["shift"])
    if multiplier <= 0 or shift != 60000:
        raise DukascopyProbeError("dukascopy_unexpected_minute_scale")
    scale_text = str(multiplier).lower()
    coefficient, _, exponent_text = scale_text.partition("e")
    decimals = len(coefficient.split(".")[1]) if "." in coefficient else 0
    scale = max(0, decimals - (int(exponent_text) if exponent_text else 0))
    stamp = int(data["timestamp"])
    price_units = {
        key: round(float(data[key]) / multiplier)
        for key in ("open", "high", "low", "close")
    }
    result = []
    for index in range(size):
        delta = times[index]
        values = (delta, opens[index], highs[index], lows[index], closes[index], volumes[index])
        if not all(_finite_number(value) for value in values) or int(delta) < 0:
            raise DukascopyProbeError("dukascopy_candle_delta_invalid")
        stamp += int(delta) * shift
        for key, column in (
            ("open", opens), ("high", highs), ("low", lows), ("close", closes)
        ):
            price_units[key] += int(column[index])
        row = {
            "timestamp_ms": stamp,
            "open": round(price_units["open"] * multiplier, scale),
            "high": round(price_units["high"] * multiplier, scale),
            "low": round(price_units["low"] * multiplier, scale),
            "close": round(price_units["close"] * multiplier, scale),
        }
        if (
            row["low"] <= 0
            or row["high"] < max(row["open"], row["close"], row["low"])
            or row["low"] > min(row["open"], row["close"])
        ):
            raise DukascopyProbeError("dukascopy_ohlc_invalid")
        result.append(row)
    return result


def _aggregate_closed_minutes(
    minutes: list[dict[str, Any]], interval_minutes: int, now_ms: int
) -> list[dict[str, Any]]:
    width_ms = interval_minutes * 60000
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in minutes:
        stamp = int(row["timestamp_ms"])
        # The source minute must itself be closed.
        if stamp + 60000 > now_ms:
            continue
        bucket = (stamp // width_ms) * width_ms
        grouped.setdefault(bucket, []).append(row)

    result = []
    for bucket, rows in sorted(grouped.items()):
        rows.sort(key=lambda item: item["timestamp_ms"])
        expected = [bucket + i * 60000 for i in range(interval_minutes)]
        actual = [int(row["timestamp_ms"]) for row in rows]
        if actual != expected or bucket + width_ms > now_ms:
            continue
        result.append({
            "time": _iso_ms(bucket),
            "close_time": _iso_ms(bucket + width_ms),
            "open": rows[0]["open"],
            "high": max(row["high"] for row in rows),
            "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
            "is_closed": True,
        })
    return result


def diagnose_dukascopy() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    hour_start_ms = (now_ms // 3600000) * 3600000
    day_start_ms = (now_ms // 86400000) * 86400000

    report: dict[str, Any] = {
        "provider": "dukascopy",
        "symbol": "XAU/USD",
        "provider_symbol": SYMBOL_CODE,
        "checked_at": now.isoformat(),
        "research_only": True,
        "signals_activated": False,
        "execution_price": False,
        "vantage_verified": False,
    }

    try:
        tick_payload = _get_json("/ticks/" + SYMBOL_CODE, hour_start_ms)
        ticks = _decode_ticks(tick_payload)
        if not ticks:
            raise DukascopyProbeError("dukascopy_no_ticks")
        last_tick = ticks[-1]
        age = (now_ms - int(last_tick["timestamp_ms"])) / 1000
        bid, ask = float(last_tick["bid"]), float(last_tick["ask"])
        tick_fresh = -1 <= age <= MAX_TICK_AGE_SECONDS
        quote_valid = bid > 0 and ask >= bid
        report["tick"] = {
            "count": len(ticks),
            "timestamp": last_tick["timestamp"],
            "age_seconds": round(age, 1),
            "bid": bid,
            "ask": ask,
            "spread": round(ask - bid, 6),
            "fresh": bool(tick_fresh),
            "bid_ask_valid": bool(quote_valid),
        }
        report["tick_fresh"] = bool(tick_fresh and quote_valid)

        minute_payload = _get_json(
            "/candles/minute/" + SYMBOL_CODE + "/BID", day_start_ms
        )
        minutes = _decode_minutes(minute_payload)
        m5 = _aggregate_closed_minutes(minutes, 5, now_ms)
        m15 = _aggregate_closed_minutes(minutes, 15, now_ms)

        def candle_status(rows: list[dict[str, Any]], interval: int) -> dict[str, Any]:
            if not rows:
                return {
                    "available": False,
                    "fresh": False,
                    "closed_count": 0,
                    "last_closed": None,
                }
            latest = rows[-1]
            close_ms = int(datetime.fromisoformat(
                latest["close_time"].replace("Z", "+00:00")
            ).timestamp() * 1000)
            age_seconds = (now_ms - close_ms) / 1000
            fresh = -5 <= age_seconds <= (interval * 120 + 90)
            return {
                "available": True,
                "fresh": bool(fresh),
                "closed_count": len(rows),
                "last_closed_age_seconds": round(age_seconds, 1),
                "last_closed": latest,
            }

        report["candles"] = {
            "m5": candle_status(m5, 5),
            "m15": candle_status(m15, 15),
        }
        all_verified = (
            report["tick_fresh"]
            and report["candles"]["m5"]["available"]
            and report["candles"]["m5"]["fresh"]
            and report["candles"]["m15"]["available"]
            and report["candles"]["m15"]["fresh"]
        )
        report["verdict"] = (
            "LIVE_DATA_VERIFIED_RESEARCH_ONLY"
            if all_verified else "PARTIAL_OR_STALE_DATA"
        )
        report["data_verified"] = bool(all_verified)
        return report
    except (requests.RequestException, DukascopyProbeError, ValueError, TypeError, KeyError) as exc:
        report.update({
            "data_verified": False,
            "tick_fresh": False,
            "verdict": "DUKASCOPY_PROBE_FAILED",
            "error": str(exc) if isinstance(exc, DukascopyProbeError) else type(exc).__name__,
        })
        return report
