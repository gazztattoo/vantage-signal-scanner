"""Normalize biquote public MT5-feed data for isolated research.

This module performs no network access. Callers must supply a response obtained
from biquote's documented public endpoints. Never use this feed as a substitute
for broker-specific Vantage quotes without basis/spread validation.
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
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_biquote_ohlc(payload: Any, *, symbol: str, interval: str,
                            now: datetime | None = None,
                            max_last_closed_age_minutes: int | None = None) -> dict:
    """Return closed bars only, validating instrument, interval and OHLC integrity.

    biquote prepends the currently forming bar with isOpen=true. It is always
    discarded. Optional freshness enforcement applies to the newest closed bar.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return {"accepted": False, "reason": "La hora de referencia debe incluir zona horaria."}
    now = now.astimezone(timezone.utc)

    expected = SYMBOLS.get(symbol)
    if expected is None or interval not in INTERVALS:
        return {"accepted": False, "reason": "Activo o temporalidad no admitidos."}
    if not isinstance(payload, dict) or payload.get("symbol") != expected:
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
        vals = [bar.get(k) for k in ("open", "high", "low", "close")]
        if stamp is None or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in vals):
            return {"accepted": False, "reason": "Vela cerrada con campos inválidos."}
        o, h, l, c = map(float, vals)
        if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
            return {"accepted": False, "reason": "OHLC inconsistente."}
        closed.append({
            "open_time": stamp.isoformat(),
            "open": o, "high": h, "low": l, "close": c,
            "tick_volume": bar.get("tickVolume"),
        })

    if not closed:
        return {"accepted": False, "reason": "No hay velas cerradas; se bloquea el análisis."}

    closed.sort(key=lambda x: x["open_time"])
    newest = _parse_utc(closed[-1]["open_time"])
    age = (now - newest).total_seconds() / 60
    if age < -1:
        return {"accepted": False, "reason": "La última vela tiene una fecha futura."}
    if max_last_closed_age_minutes is not None and age > max_last_closed_age_minutes:
        return {"accepted": False, "reason": "La última vela cerrada está desactualizada.",
                "age_minutes": round(age, 1)}

    return {
        "accepted": True,
        "provider": "biquote",
        "source": "MT5 aggregated feed",
        "symbol": symbol,
        "provider_symbol": expected,
        "interval": interval,
        "bars": closed,
        "closed_bars": len(closed),
        "last_closed_open_time": closed[-1]["open_time"],
        "last_closed_age_minutes": round(age, 1),
        "price_basis": "provider_feed_not_verified_as_VANTAGE",
        "volume_warning": "FX/CFD volume is not traded volume; use tickVolume only as tick count.",
        "role": "RESEARCH_ONLY_UNTIL_BROKER_BASIS_VALIDATED",
    }


def normalize_biquote_tick(payload: Any, *, symbol: str,
                           now: datetime | None = None,
                           max_age_seconds: int = 60) -> dict:
    """Validate one quote snapshot; stale/closed quotes are rejected for live use."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return {"accepted": False, "reason": "La hora de referencia debe incluir zona horaria."}
    now = now.astimezone(timezone.utc)
    expected = SYMBOLS.get(symbol)
    if expected is None or not isinstance(payload, dict) or payload.get("symbol") != expected:
        return {"accepted": False, "reason": "Cotización o activo inválido."}
    if payload.get("marketState") != "open" or payload.get("stale") is True:
        return {"accepted": False, "reason": "Mercado cerrado o cotización obsoleta."}
    bid, ask = payload.get("bid"), payload.get("ask")
    stamp = _parse_utc(payload.get("timestamp"))
    if (isinstance(bid, bool) or isinstance(ask, bool) or
        not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)) or
        bid <= 0 or ask < bid or stamp is None):
        return {"accepted": False, "reason": "Bid/Ask o timestamp inválidos."}
    age = (now - stamp).total_seconds()
    if age < -1 or age > max_age_seconds:
        return {"accepted": False, "reason": "Cotización fuera de la ventana de frescura.",
                "quote_age_seconds": round(age, 1)}
    return {
        "accepted": True, "provider": "biquote", "symbol": symbol,
        "bid": float(bid), "ask": float(ask), "mid": (float(bid) + float(ask)) / 2,
        "spread": float(ask) - float(bid), "timestamp": stamp.isoformat(),
        "quote_age_seconds": round(age, 1),
        "price_basis": "provider_feed_not_verified_as_VANTAGE",
        "role": "REFERENCE_ONLY_NOT_EXECUTION_PRICE",
    }
