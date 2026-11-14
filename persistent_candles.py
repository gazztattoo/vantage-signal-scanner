"""Persistent one-minute OHLC candles built from BiQuote SignalR ticks.

The stream is an external reference feed, never a Vantage execution quote.
Only completed UTC minutes are persisted. Missing minutes are not fabricated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _utc_minute(value: str | datetime) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    stamp = stamp.astimezone(timezone.utc)
    return stamp.replace(second=0, microsecond=0)


class MinuteCandleBuilder:
    def __init__(self) -> None:
        self._active: dict[tuple[str, datetime], dict[str, Any]] = {}

    def add_tick(self, tick: dict[str, Any]) -> list[dict[str, Any]]:
        symbol = str(tick.get("symbol", "")).upper()
        if symbol not in {"EUR/USD", "XAU/USD", "BTC/USD"}:
            return []
        if tick.get("stale") or tick.get("market_state") == "closed":
            return []
        price = float(tick.get("mid", 0))
        if price <= 0:
            return []
        stamp = datetime.fromisoformat(str(tick["timestamp"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return []
        minute = _utc_minute(stamp)
        completed = []
        # A tick advances this symbol's stream. Finalize only earlier active minutes.
        for key, candle in list(self._active.items()):
            if key[0] == symbol and key[1] < minute:
                completed.append(candle)
                del self._active[key]
        key = (symbol, minute)
        candle = self._active.get(key)
        if candle is None:
            candle = {
                "symbol": symbol,
                "minute_open": minute.isoformat().replace("+00:00", "Z"),
                "open": price, "high": price, "low": price, "close": price,
                "tick_count": 1, "source": "biquote_signalr",
            }
            self._active[key] = candle
        else:
            candle["high"] = max(candle["high"], price)
            candle["low"] = min(candle["low"], price)
            candle["close"] = price
            candle["tick_count"] += 1
        return completed

    def flush(self, symbol: str, now: datetime | None = None) -> list[dict[str, Any]]:
        """Finalize active candles only after their minute has fully elapsed."""
        current = _utc_minute(now or datetime.now(timezone.utc))
        result = []
        for key, candle in list(self._active.items()):
            if key[0] == symbol and key[1] < current:
                result.append(candle)
                del self._active[key]
        return result


def aggregate_minutes(rows: list[dict[str, Any]], interval_minutes: int, limit: int = 120) -> list[dict[str, Any]]:
    """Build complete M5/M15 candles; any missing constituent minute drops the bar."""
    if interval_minutes not in (5, 15):
        raise ValueError("only 5 and 15 minute candles are supported")
    by_minute: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        stamp = _utc_minute(row["minute_open"])
        by_minute[stamp] = row
    buckets: dict[datetime, list[tuple[datetime, dict[str, Any]]]] = {}
    for stamp, row in by_minute.items():
        epoch_minute = int(stamp.timestamp() // 60)
        start = datetime.fromtimestamp((epoch_minute // interval_minutes) * interval_minutes * 60, timezone.utc)
        buckets.setdefault(start, []).append((stamp, row))
    candles = []
    for start, items in sorted(buckets.items()):
        items.sort(key=lambda pair: pair[0])
        if len(items) != interval_minutes:
            continue
        expected = [start + timedelta(minutes=i) for i in range(interval_minutes)]
        if [stamp for stamp, _ in items] != expected:
            continue
        values = [row for _, row in items]
        candles.append({
            "time": start.isoformat(),
            "open": float(values[0]["open"]),
            "high": max(float(row["high"]) for row in values),
            "low": min(float(row["low"]) for row in values),
            "close": float(values[-1]["close"]),
        })
    return candles[-limit:]
