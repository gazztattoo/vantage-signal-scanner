"""Read-only SignalR diagnostics for BiQuote TEST.

This module never supplies market data to the trading engine. It observes
subscription state, ticks and candle events only.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from typing import Any

from pysignalr.client import SignalRClient

HUB_URL = "https://biquote.io/hubs/tick"
SYMBOL = "XAUUSD"


def _unwrap(payload: Any) -> dict | None:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            payload = ast.literal_eval(payload)
        except (ValueError, SyntaxError):
            return None
    if isinstance(payload, (list, tuple)):
        for item in payload:
            result = _unwrap(item)
            if result is not None:
                return result
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc)
    except ValueError:
        return None


async def _probe(timeout_seconds: int) -> dict:
    report: dict[str, Any] = {
        "provider": "biquote",
        "transport": "signalr",
        "hub_url": HUB_URL,
        "symbol": SYMBOL,
        "connected": False,
        "subscription_sent": False,
        "tick_received": False,
        "tick_count": 0,
        "tick_fresh": False,
        "subscription_state_received": False,
        "subscription_state": None,
        "candle_count": 0,
        "candles": [],
        "research_only": True,
        "signals_activated": False,
        "execution_price": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    client = SignalRClient(HUB_URL)

    async def on_open() -> None:
        report["connected"] = True
        await client.send("Subscribe", [[SYMBOL]])
        await client.send("SubscribeCandles", [[SYMBOL], "5m"])
        await client.send("SubscribeCandles", [[SYMBOL], "15m"])
        report["subscription_sent"] = True

    async def on_tick(message: Any) -> None:
        tick = _unwrap(message)
        if not tick or str(tick.get("symbol", "")).upper() != SYMBOL:
            return
        report["tick_received"] = True
        report["tick_count"] += 1
        stamp = _parse_timestamp(tick.get("timestamp"))
        age = (datetime.now(timezone.utc) - stamp).total_seconds() if stamp else None
        bid, ask = tick.get("bid"), tick.get("ask")
        valid_prices = (
            isinstance(bid, (int, float)) and not isinstance(bid, bool) and bid > 0
            and isinstance(ask, (int, float)) and not isinstance(ask, bool) and ask >= bid
        )
        fresh = age is not None and -1 <= age <= 60
        report["tick"] = {
            "symbol": SYMBOL,
            "timestamp": stamp.isoformat() if stamp else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "bid_ask_valid": bool(valid_prices),
            "fresh": bool(fresh),
        }
        report["tick_fresh"] = bool(fresh and valid_prices)

    async def on_subscription_state(message: Any) -> None:
        state = _unwrap(message)
        report["subscription_state_received"] = True
        if state is None:
            report["subscription_state"] = str(message)[:300]
            return
        report["subscription_state"] = {
            key: state.get(key)
            for key in ("symbol", "state", "marketState", "status", "isOpen",
                        "quoteAgeSeconds", "seeded", "message")
            if key in state
        }

    async def on_candle(message: Any) -> None:
        candle = _unwrap(message)
        if not candle or str(candle.get("symbol", "")).upper() != SYMBOL:
            return
        report["candle_count"] += 1
        item = {
            key: candle.get(key)
            for key in ("symbol", "interval", "timeframe", "period", "openTime",
                        "timestamp", "time", "open", "high", "low", "close")
            if key in candle
        }
        report["candles"] = (report["candles"] + [item])[-6:]

    async def on_error(error: Any) -> None:
        report["client_error"] = type(error).__name__

    client.on_open(on_open)
    client.on("ReceiveTick", on_tick)
    client.on("ReceiveSubscriptionState", on_subscription_state)
    client.on("ReceiveCandle", on_candle)
    client.on_error(on_error)

    task = asyncio.create_task(client.run())
    try:
        # Keep the connection open for the full diagnostic window so the
        # subscription state and both candle streams have time to arrive.
        await asyncio.sleep(timeout_seconds)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    report["timeout_seconds"] = timeout_seconds
    if not report["connected"]:
        report["verdict"] = "SIGNALR_CONNECTION_TIMEOUT"
    elif report["tick_fresh"]:
        report["verdict"] = "LIVE_SIGNALR_TICK_VERIFIED"
    elif report["tick_received"]:
        report["verdict"] = "CONNECTED_STALE_TICK_ONLY"
    else:
        report["verdict"] = "CONNECTED_NO_XAUUSD_TICK"
    report["signals_activated"] = False
    report["execution_price"] = False
    return report


def probe_biquote_signalr(timeout_seconds: int = 20) -> dict:
    """Open a short-lived SignalR connection and inspect live market events."""
    try:
        return asyncio.run(_probe(max(5, min(int(timeout_seconds), 30))))
    except Exception as exc:
        return {
            "provider": "biquote",
            "transport": "signalr",
            "connected": False,
            "tick_received": False,
            "tick_fresh": False,
            "verdict": "SIGNALR_PROBE_FAILED",
            "error": type(exc).__name__,
            "research_only": True,
            "signals_activated": False,
            "execution_price": False,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
