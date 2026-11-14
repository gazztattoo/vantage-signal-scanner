"""BiQuote SignalR stream worker and in-memory latest-tick cache.

One upstream SignalR connection per application process; no REST polling.
Ticks are research/feed observations, not Vantage execution quotes.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from pysignalr.client import SignalRClient

LOGGER = logging.getLogger(__name__)
HUB_URL = "https://biquote.io/hubs/tick"
SYMBOLS = ("EURUSD", "XAUUSD", "BTCUSD")
SYMBOL_MAP = {"EURUSD": "EUR/USD", "XAUUSD": "XAU/USD", "BTCUSD": "BTC/USD"}

_lock = threading.RLock()
_latest: dict[str, dict[str, Any]] = {}
_state: dict[str, Any] = {
    "enabled": False, "status": "stopped", "connected": False,
    "started_at": None, "last_tick_at": None, "last_error": None,
}
_thread: threading.Thread | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_tick(message: Any) -> dict[str, Any] | None:
    # pysignalr dispatches hub arguments as a list; accept a direct dict too.
    tick = message[0] if isinstance(message, list) and message else message
    if not isinstance(tick, dict):
        return None
    raw_symbol = str(tick.get("symbol", "")).upper().replace("/", "")
    symbol = SYMBOL_MAP.get(raw_symbol)
    if not symbol:
        return None
    try:
        bid, ask = float(tick["bid"]), float(tick["ask"])
        mid = float(tick.get("mid") or ((bid + ask) / 2))
    except (KeyError, TypeError, ValueError):
        return None
    if bid <= 0 or ask < bid or mid <= 0:
        return None
    raw_time = tick.get("timestamp") or tick.get("lastQuoteAt")
    try:
        stamp = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00")) if raw_time else datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            return None
        age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return None
    if age < -10 or age > 300:
        return None
    return {
        "symbol": symbol, "provider_symbol": raw_symbol,
        "bid": bid, "ask": ask, "mid": mid, "spread": ask - bid,
        "timestamp": stamp.astimezone(timezone.utc).isoformat(),
        "received_at": _utc_now(), "quote_age_seconds": round(max(age, 0), 1),
        "market_state": tick.get("marketState", "unknown"),
        "stale": bool(tick.get("stale", False)),
        "source": "biquote_external_mt5_feed",
        "is_vantage_quote": False,
    }


def publish_tick(message: Any) -> bool:
    tick = _extract_tick(message)
    if not tick:
        return False
    with _lock:
        _latest[tick["symbol"]] = tick
        _state["last_tick_at"] = tick["received_at"]
    return True


def snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "stream": dict(_state),
            "ticks": {key: dict(value) for key, value in _latest.items()},
            "provider": "biquote",
            "is_vantage_quote": False,
        }


def _set_state(**values: Any) -> None:
    with _lock:
        _state.update(values)


async def _run_client() -> None:
    client = SignalRClient(
        HUB_URL, ping_interval=15, connection_timeout=20,
        retry_sleep=2, retry_multiplier=1.5, retry_count=0,
    )

    async def on_open() -> None:
        _set_state(status="connected", connected=True, last_error=None)
        LOGGER.info("biquote_stream connected")
        await client.send("Subscribe", [list(SYMBOLS)])

    async def on_close() -> None:
        _set_state(status="reconnecting", connected=False)
        LOGGER.warning("biquote_stream disconnected")

    async def on_error(error: Any) -> None:
        _set_state(last_error=str(getattr(error, "error", error))[:240])
        LOGGER.warning("biquote_stream protocol_error=%s", str(error)[:240])

    async def on_tick(message: Any) -> None:
        if not publish_tick(message):
            LOGGER.debug("biquote_stream ignored invalid tick")

    client.on_open(on_open)
    client.on_close(on_close)
    client.on_error(on_error)
    client.on("ReceiveTick", on_tick)
    await client.run()


def _thread_main() -> None:
    _set_state(status="connecting", connected=False, started_at=_utc_now(), last_error=None)
    while True:
        try:
            asyncio.run(_run_client())
            _set_state(status="disconnected", connected=False)
        except Exception as exc:
            _set_state(status="reconnecting", connected=False, last_error=str(exc)[:240])
            LOGGER.exception("biquote_stream connection failed")
        time.sleep(3)


def start_stream() -> bool:
    global _thread
    with _lock:
        if _thread and _thread.is_alive():
            return False
        _thread = threading.Thread(target=_thread_main, name="biquote-signalr", daemon=True)
        _thread.start()
        _state.update({"enabled": True, "status": "starting"})
        return True
