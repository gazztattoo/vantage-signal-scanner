"""BiQuote SignalR stream worker and in-memory latest-tick cache.

One upstream SignalR connection per application process; no REST polling.
Ticks are research/feed observations, not Vantage execution quotes.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from datetime import datetime, timezone
from typing import Any


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
HUB_URL = "https://biquote.io/hubs/tick"
SYMBOL_MAP = {"EURUSD": "EUR/USD", "XAUUSD": "XAU/USD"}
_requested_symbols = [
    item.strip().upper().replace("/", "")
    for item in os.getenv("BIQUOTE_STREAM_SYMBOLS", "EURUSD,XAUUSD").split(",")
    if item.strip()
]
SYMBOLS = tuple(dict.fromkeys(item for item in _requested_symbols if item in SYMBOL_MAP))

_lock = threading.RLock()
_latest: dict[str, dict[str, Any]] = {}
_CANDLE_SECONDS = {"5min": 300, "15min": 900, "30min": 1800}
_CLOSED_CANDLES: dict[tuple[str, str], list[dict[str, Any]]] = {}
_FORMING_CANDLES: dict[tuple[str, str], dict[str, Any]] = {}
_MAX_CANDLES = 240
_state: dict[str, Any] = {
    "enabled": False, "status": "stopped", "connected": False,
    "started_at": None, "connected_at": None, "last_tick_at": None,
    "last_error": None, "retry_attempt": 0, "retry_in_seconds": 0,
    "subscription_state": "not_started",
    "tick_events_received": 0, "tick_events_accepted": 0,
    "tick_events_rejected": 0, "last_tick_event_at": None,
    "last_tick_event_shape": None,
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
    raw_time = tick.get("time") or tick.get("timestamp") or tick.get("lastQuoteAt")
    # Never manufacture a current timestamp: an undated payload cannot be proven fresh.
    if not raw_time:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
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


def _parse_candle_stamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def seed_history(symbol: str, interval: str, candles: list[dict[str, Any]]) -> int:
    """Seed the local builder with provider-closed OHLC bars; no network calls."""
    if symbol not in ("EUR/USD", "XAU/USD") or interval not in _CANDLE_SECONDS:
        return 0
    seconds = _CANDLE_SECONDS[interval]
    seeded = []
    for item in candles:
        stamp = _parse_candle_stamp(item.get("time"))
        if stamp is None:
            continue
        try:
            values = {key: float(item[key]) for key in ("open", "high", "low", "close")}
        except (KeyError, TypeError, ValueError):
            continue
        if min(values.values()) <= 0 or values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"], values["high"]):
            continue
        bucket = int(stamp.timestamp()) // seconds * seconds
        seeded.append({
            "time": datetime.fromtimestamp(bucket, timezone.utc).isoformat(),
            **values,
            "source": "biquote_ohlc_seed",
        })
    seeded.sort(key=lambda item: item["time"])
    deduped = {item["time"]: item for item in seeded}
    with _lock:
        key = (symbol, interval)
        existing = {item["time"]: item for item in _CLOSED_CANDLES.get(key, [])}
        existing.update(deduped)
        _CLOSED_CANDLES[key] = [existing[k] for k in sorted(existing)][-_MAX_CANDLES:]
    return len(seeded)


def _update_local_candles(tick: dict[str, Any]) -> None:
    stamp = _parse_candle_stamp(tick.get("timestamp"))
    if stamp is None:
        return
    price = float(tick["mid"])
    with _lock:
        for interval, seconds in _CANDLE_SECONDS.items():
            key = (tick["symbol"], interval)
            bucket = int(stamp.timestamp()) // seconds * seconds
            bucket_time = datetime.fromtimestamp(bucket, timezone.utc).isoformat()
            forming = _FORMING_CANDLES.get(key)
            history = _CLOSED_CANDLES.get(key, [])
            # A late tick for an already closed/seeded interval is never
            # allowed to create a candle older than the current history.
            if history and bucket_time <= history[-1]["time"] and not (forming and forming["time"] == bucket_time):
                continue
            if forming and forming["time"] == bucket_time:
                forming["high"] = max(forming["high"], price)
                forming["low"] = min(forming["low"], price)
                forming["close"] = price
                forming["tick_count"] += 1
                forming["last_tick_at"] = tick["timestamp"]
            elif not forming or bucket > int(datetime.fromisoformat(forming["time"]).timestamp() // seconds) * seconds:
                if forming and bucket > int(datetime.fromisoformat(forming["time"]).timestamp()):
                    closed = {k: v for k, v in forming.items() if k not in ("tick_count", "last_tick_at")}
                    closed["source"] = "biquote_live_ticks"
                    history = _CLOSED_CANDLES.setdefault(key, [])
                    if not history or history[-1]["time"] < closed["time"]:
                        history.append(closed)
                        del history[:-_MAX_CANDLES]
                if not forming or bucket > int(datetime.fromisoformat(forming["time"]).timestamp()):
                    _FORMING_CANDLES[key] = {
                        "time": bucket_time, "open": price, "high": price,
                        "low": price, "close": price, "tick_count": 1,
                        "last_tick_at": tick["timestamp"],
                    }


def closed_candles(symbol: str, interval: str) -> list[dict[str, Any]]:
    with _lock:
        return [dict(item) for item in _CLOSED_CANDLES.get((symbol, interval), [])]


def publish_tick(message: Any) -> bool:
    tick = _extract_tick(message)
    if not tick or tick.get("stale"):
        return False
    with _lock:
        previous = _latest.get(tick["symbol"])
        # Reject out-of-order provider events so an old packet cannot roll
        # the live quote backwards or contaminate a newer candle.
        if previous and tick["timestamp"] < previous["timestamp"]:
            return False
        _latest[tick["symbol"]] = tick
        _state["last_tick_at"] = tick["received_at"]
    _update_local_candles(tick)
    return True


def snapshot() -> dict[str, Any]:
    with _lock:
        candle_status = {}
        for symbol in SYMBOL_MAP.values():
            for interval in _CANDLE_SECONDS:
                key = (symbol, interval)
                history = _CLOSED_CANDLES.get(key, [])
                forming = _FORMING_CANDLES.get(key)
                candle_status[f"{symbol}|{interval}"] = {
                    "closed_count": len(history),
                    "last_closed_time": history[-1]["time"] if history else None,
                    "forming_time": forming.get("time") if forming else None,
                    "forming_tick_count": forming.get("tick_count", 0) if forming else 0,
                }
        now = datetime.now(timezone.utc)
        ticks = {key: dict(value) for key, value in _latest.items()}
        for tick in ticks.values():
            try:
                stamp = datetime.fromisoformat(tick["timestamp"].replace("Z", "+00:00"))
                tick["age_seconds"] = round(max((now - stamp.astimezone(timezone.utc)).total_seconds(), 0), 1)
                tick["fresh"] = tick["age_seconds"] <= 90 and not tick.get("stale")
            except (KeyError, TypeError, ValueError):
                tick["age_seconds"] = None
                tick["fresh"] = False
        stream_state = dict(_state)
        stream_state["fresh_symbols"] = sorted(k for k, v in ticks.items() if v["fresh"])
        stream_state["stale_symbols"] = sorted(k for k, v in ticks.items() if not v["fresh"])
        if stream_state.get("connected") and not stream_state["fresh_symbols"]:
            stream_state["quote_health"] = "waiting_for_fresh_ticks"
        elif stream_state["fresh_symbols"]:
            stream_state["quote_health"] = "fresh"
        else:
            stream_state["quote_health"] = "no_fresh_quotes"
        return {
            "stream": stream_state,
            "ticks": ticks,
            "candles": candle_status,
            "provider": "biquote",
            "is_vantage_quote": False,
        }


def _set_state(**values: Any) -> None:
    with _lock:
        _state.update(values)


async def _run_client() -> None:
    # Import only when streaming is explicitly enabled. Other API tests and
    # routes must remain importable without the optional SignalR dependency.
    from pysignalr.client import SignalRClient

    # The outer worker owns retries to prevent a rapid 504 negotiation loop.
    client = SignalRClient(
        HUB_URL, ping_interval=15, connection_timeout=30,
        retry_sleep=2, retry_multiplier=1.5, retry_count=0,
    )

    async def on_open() -> None:
        _set_state(status="connected", connected=True, last_error=None,
                   connected_at=_utc_now(), subscription_state="pending")
        LOGGER.info("biquote_stream connected; subscribing symbols=%s", ",".join(SYMBOLS))
        try:
            await client.send("Subscribe", [list(SYMBOLS)])
        except Exception as exc:
            _set_state(status="reconnecting", connected=False,
                       last_error=("subscription_send: " + str(exc))[:240])
            LOGGER.exception("biquote_stream subscription send failed")
            raise

    async def on_close() -> None:
        _set_state(status="reconnecting", connected=False)
        LOGGER.warning("biquote_stream disconnected; cached quotes will age out")

    async def on_error(error: Any) -> None:
        _set_state(last_error=str(getattr(error, "error", error))[:240])
        LOGGER.warning("biquote_stream protocol_error=%s", str(error)[:240])

    async def on_tick(message: Any) -> None:
        accepted = publish_tick(message)
        with _lock:
            _state["tick_events_received"] = int(_state.get("tick_events_received", 0)) + 1
            key = "tick_events_accepted" if accepted else "tick_events_rejected"
            _state[key] = int(_state.get(key, 0)) + 1
            _state["last_tick_event_at"] = _utc_now()
            # Record only the envelope shape, never the raw market payload.
            _state["last_tick_event_shape"] = (
                "list:" + type(message[0]).__name__ if isinstance(message, list) and message
                else "empty_list" if isinstance(message, list)
                else type(message).__name__
            )
        if not accepted:
            LOGGER.warning("biquote_stream received an unrecognized or stale tick event")

    async def on_subscription_state(message: Any) -> None:
        # BiQuote emits this hub event after Subscribe. Register it explicitly
        # so SignalR does not report an unhandled client method. Keep the raw
        # payload bounded and diagnostic-only; it never validates a trade.
        state_text = str(message)[:240]
        _set_state(subscription_state=state_text)
        LOGGER.info("biquote_stream subscription_state=%s", state_text)

    client.on_open(on_open)
    client.on_close(on_close)
    client.on_error(on_error)
    client.on("ReceiveTick", on_tick)
    client.on("ReceiveSubscriptionState", on_subscription_state)
    await client.run()


def _retry_delay(attempt: int) -> float:
    """Bounded exponential retry with jitter for transient upstream failures."""
    attempt = max(int(attempt), 1)
    base = min(3.0 * (2 ** min(attempt - 1, 5)), 60.0)
    return min(60.0, base * random.uniform(0.8, 1.2))


def _thread_main() -> None:
    failures = 0
    LOGGER.info("biquote_stream worker started symbols=%s hub=%s", ",".join(SYMBOLS), HUB_URL)
    _set_state(status="connecting", connected=False, started_at=_utc_now(),
               last_error=None, retry_attempt=0, retry_in_seconds=0)
    while True:
        attempt_started = time.monotonic()
        try:
            asyncio.run(_run_client())
            session_seconds = time.monotonic() - attempt_started
            if session_seconds >= 60:
                failures = 0
            failures += 1
            _set_state(status="reconnecting", connected=False,
                       last_error="SignalR terminó la conexión sin error explícito.")
            LOGGER.warning("biquote_stream client ended session_seconds=%.1f", session_seconds)
        except Exception as exc:
            session_seconds = time.monotonic() - attempt_started
            if session_seconds >= 60:
                failures = 0
            failures += 1
            _set_state(status="reconnecting", connected=False, last_error=str(exc)[:240])
            LOGGER.exception("biquote_stream connection failed attempt=%s session_seconds=%.1f",
                             failures, session_seconds)
        delay = _retry_delay(failures)
        _set_state(retry_attempt=failures, retry_in_seconds=round(delay, 1))
        LOGGER.warning("biquote_stream reconnect scheduled in %.1fs attempt=%s", delay, failures)
        time.sleep(delay)


def start_stream() -> bool:
    global _thread
    if not SYMBOLS:
        _set_state(enabled=False, status="disabled", connected=False,
                   last_error="No hay símbolos BiQuote autorizados configurados.")
        return False
    with _lock:
        if _thread and _thread.is_alive():
            return False
        # Publish the desired state before starting the worker; otherwise the
        # caller can overwrite the worker's "connecting" state after it starts.
        _state.update({"enabled": True, "status": "starting"})
        _thread = threading.Thread(target=_thread_main, name="biquote-signalr", daemon=True)
        _thread.start()
        LOGGER.info("biquote_stream worker launch requested symbols=%s", ",".join(SYMBOLS))
        return True
