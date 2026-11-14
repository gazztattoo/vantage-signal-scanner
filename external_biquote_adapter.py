"""Normalize BiQuote OHLC and ticks without performing network requests.

The adapter is intentionally pure: callers supply provider payloads. BiQuote is
an external MT5 feed and must not be treated as a Vantage execution quote until
the broker basis and spread have been validated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SYMBOLS = {"XAU/USD": "XAUUSD", "EUR/USD": "EURUSD", "BTC/USD": "BTCUSD"}
INTERVALS = {"5m", "15m", "30m"}


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_biquote_ohlc(
    payload: Any,
    *,
    symbol: str,
    interval: str,
    now: datetime | None = None,
    max_last_closed_age_minutes: int | None = None,
) -> dict:
    """Validate a BiQuote response and return closed, chronological bars only."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return {"accepted": False, "reason": "La hora de referencia debe incluir zona horaria."}
    now = now.astimezone(timezone.utc)

    expected_symbol = SYMBOLS.get(symbol)
    if expected_symbol is None or interval not in INTERVALS:
        return {"accepted": False, "reason": "Activo o temporalidad no admitidos."}
    if not isinstance(payload, dict) or payload.get("symbol") != expected_symbol:
        return {"accepted": False, "reason": "La respuesta no corresponde al activo solicitado."}
    if payload.get("interval") != interval:
        return {"accepted": False, "reason": "La respuesta no corresponde a la temporalidad solicitada."}

    raw_bars = payload.get("bars")
    if not isinstance(raw_bars, list) or not raw_bars:
        return {"accepted": False, "reason": "No hay velas disponibles."}

    closed = []
    for bar in raw_bars:
        if not isinstance(bar, dict) or bar.get("isOpen") is True:
            continue
        stamp = _parse_utc(bar.get("openTime"))
        values = [bar.get(key) for key in ("open", "high", "low", "close")]
        if stamp is None or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            return {"accepted": False, "reason": "Vela cerrada con campos inválidos."}
        opened, high, low, close = map(float, values)
        if (
            min(opened, high, low, close) <= 0
            or high < max(opened, close, low)
            or low > min(opened, close, high)
        ):
            return {"accepted": False, "reason": "OHLC inconsistente."}
        closed.append({
            "time": stamp.isoformat(),
            "open": opened,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": bar.get("tickVolume"),
        })

    if not closed:
        return {"accepted": False, "reason": "No hay velas cerradas; se bloquea el análisis."}

    closed.sort(key=lambda item: item["time"])
    newest = _parse_utc(closed[-1]["time"])
    age_minutes = (now - newest).total_seconds() / 60
    if age_minutes < -1:
        return {"accepted": False, "reason": "La última vela tiene una fecha futura."}
    if (
        max_last_closed_age_minutes is not None
        and age_minutes > max_last_closed_age_minutes
    ):
        return {
            "accepted": False,
            "reason": "La última vela cerrada está desactualizada.",
            "age_minutes": round(age_minutes, 1),
        }

    return {
        "accepted": True,
        "provider": "biquote",
        "source": "MT5 aggregated feed",
        "symbol": symbol,
        "provider_symbol": expected_symbol,
        "interval": interval,
        "bars": closed,
        "closed_bars": len(closed),
        "last_closed_open_time": closed[-1]["time"],
        "last_closed_age_minutes": round(age_minutes, 1),
        "price_basis": "provider_feed_not_verified_as_VANTAGE",
        "volume_warning": "FX/CFD volume is not traded volume; tickVolume is only a tick count.",
        "role": "RESEARCH_ONLY_UNTIL_BROKER_BASIS_VALIDATED",
    }


def normalize_biquote_tick(
    payload: Any,
    *,
    symbol: str,
    now: datetime | None = None,
    max_age_seconds: int = 60,
) -> dict:
    """Validate a fresh Bid/Ask snapshot; reject closed or stale quotes."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return {"accepted": False, "reason": "La hora de referencia debe incluir zona horaria."}
    now = now.astimezone(timezone.utc)

    expected_symbol = SYMBOLS.get(symbol)
    if (
        expected_symbol is None
        or not isinstance(payload, dict)
        or payload.get("symbol") != expected_symbol
    ):
        return {"accepted": False, "reason": "Cotización o activo inválido."}
    if payload.get("marketState") != "open" or payload.get("stale") is True:
        return {"accepted": False, "reason": "Mercado cerrado o cotización obsoleta."}

    bid, ask = payload.get("bid"), payload.get("ask")
    stamp = _parse_utc(payload.get("timestamp"))
    if (
        isinstance(bid, bool)
        or isinstance(ask, bool)
        or not isinstance(bid, (int, float))
        or not isinstance(ask, (int, float))
        or bid <= 0
        or ask < bid
        or stamp is None
    ):
        return {"accepted": False, "reason": "Bid/Ask o timestamp inválidos."}

    age_seconds = (now - stamp).total_seconds()
    if age_seconds < -1 or age_seconds > max_age_seconds:
        return {
            "accepted": False,
            "reason": "Cotización fuera de la ventana de frescura.",
            "quote_age_seconds": round(age_seconds, 1),
        }

    return {
        "accepted": True,
        "provider": "biquote",
        "symbol": symbol,
        "bid": float(bid),
        "ask": float(ask),
        "mid": (float(bid) + float(ask)) / 2,
        "spread": float(ask) - float(bid),
        "timestamp": stamp.isoformat(),
        "quote_age_seconds": round(age_seconds, 1),
        "price_basis": "provider_feed_not_verified_as_VANTAGE",
        "role": "REFERENCE_ONLY_NOT_EXECUTION_PRICE",
    }
