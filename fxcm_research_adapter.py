"""Research-only parser for FXCM Socket REST historical candles.

This module performs no network requests, authentication, persistence, or trading.
FXCM's documented candle tuple is:
[timestamp, BidOpen, BidClose, BidHigh, BidLow,
 AskOpen, AskClose, AskHigh, AskLow, TickQty]

The adapter preserves Bid and Ask OHLC separately. It must not be treated as
Vantage execution data or used by the production scanner without live validation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

PERIOD_MINUTES = {"m1": 1, "m5": 5, "m15": 15, "m30": 30, "H1": 60}
RESEARCH_ROLE = "RESEARCH_ONLY_NOT_VANTAGE_EXECUTION_DATA"


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} no es numérico.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} no es numérico.") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} debe ser un precio positivo y finito.")
    return result


def _ohlc(opening: Any, closing: Any, high: Any, low: Any, side: str) -> dict:
    values = {
        "open": _number(opening, f"{side}.open"),
        "close": _number(closing, f"{side}.close"),
        "high": _number(high, f"{side}.high"),
        "low": _number(low, f"{side}.low"),
    }
    if values["high"] < max(values["open"], values["close"], values["low"]):
        raise ValueError(f"OHLC {side} inconsistente: high.")
    if values["low"] > min(values["open"], values["close"], values["high"]):
        raise ValueError(f"OHLC {side} inconsistente: low.")
    return values


def normalize_fxcm_candles(
    payload: Any,
    *,
    symbol: str,
    period: str,
    now: datetime | None = None,
    max_closed_bar_age_minutes: float | None = None,
) -> dict:
    """Parse FXCM documented candle arrays, returning completed Bid/Ask bars."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return {"accepted": False, "reason": "La hora de referencia debe incluir zona horaria."}
    now = now.astimezone(timezone.utc)

    if period not in PERIOD_MINUTES:
        return {"accepted": False, "reason": "Temporalidad FXCM no admitida."}
    if not isinstance(symbol, str) or not symbol.strip():
        return {"accepted": False, "reason": "Falta el símbolo solicitado."}
    if not isinstance(payload, dict):
        return {"accepted": False, "reason": "Respuesta FXCM inválida."}

    response = payload.get("response")
    if not isinstance(response, dict) or response.get("executed") is not True:
        return {"accepted": False, "reason": "FXCM no confirmó la consulta histórica."}
    if response.get("error") not in (None, ""):
        return {"accepted": False, "reason": "FXCM devolvió un error."}
    if str(payload.get("period_id", "")).lower() != period.lower():
        return {"accepted": False, "reason": "La temporalidad recibida no coincide."}

    instrument_id = payload.get("instrument_id")
    if instrument_id is None or str(instrument_id).strip() == "":
        return {"accepted": False, "reason": "Falta el identificador del instrumento FXCM."}

    raw = payload.get("candles")
    if not isinstance(raw, list) or not raw:
        return {"accepted": False, "reason": "FXCM no devolvió velas."}

    duration = timedelta(minutes=PERIOD_MINUTES[period])
    bars = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, (list, tuple)) or len(item) < 10:
            return {"accepted": False, "reason": f"La vela {index} no contiene los 10 campos documentados."}
        try:
            stamp_value = float(item[0])
            if not math.isfinite(stamp_value):
                raise ValueError
            stamp = datetime.fromtimestamp(stamp_value, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return {"accepted": False, "reason": f"Timestamp inválido en vela {index}."}

        if stamp in seen:
            return {"accepted": False, "reason": "FXCM devolvió timestamps duplicados."}
        seen.add(stamp)

        try:
            bid = _ohlc(item[1], item[2], item[3], item[4], "bid")
            ask = _ohlc(item[5], item[6], item[7], item[8], "ask")
            tick_qty = int(item[9])
        except (ValueError, TypeError, OverflowError) as exc:
            return {"accepted": False, "reason": str(exc)}
        if tick_qty < 0:
            return {"accepted": False, "reason": "Tick quantity negativo."}
        if ask["high"] < bid["low"] or bid["high"] > ask["low"] + (ask["high"] - ask["low"]) + (bid["high"] - bid["low"]):
            # Do not assume the broker's spread is constant across a candle;
            # only require the basic quote-side ordering at open and close.
            pass
        if ask["open"] < bid["open"] or ask["close"] < bid["close"]:
            return {"accepted": False, "reason": "Ask por debajo de Bid en apertura o cierre."}

        close_time = stamp + duration
        if close_time > now:
            continue
        bars.append({
            "time": stamp.isoformat(),
            "close_time": close_time.isoformat(),
            "bid": bid,
            "ask": ask,
            "tick_qty": tick_qty,
            "is_closed": True,
        })

    if not bars:
        return {"accepted": False, "reason": "No hay velas completamente cerradas."}

    bars.sort(key=lambda row: row["time"])
    newest_close = datetime.fromisoformat(bars[-1]["close_time"])
    age_minutes = max((now - newest_close).total_seconds() / 60, 0)
    if max_closed_bar_age_minutes is not None and age_minutes > max_closed_bar_age_minutes:
        return {
            "accepted": False,
            "reason": "La última vela cerrada está desactualizada.",
            "last_closed_age_minutes": round(age_minutes, 2),
        }

    return {
        "accepted": True,
        "provider": "fxcm",
        "symbol": symbol,
        "instrument_id": str(instrument_id),
        "period": period,
        "bars": bars,
        "closed_bars": len(bars),
        "last_closed_time": bars[-1]["close_time"],
        "last_closed_age_minutes": round(age_minutes, 2),
        "role": RESEARCH_ROLE,
        "warning": "Bid/Ask de FXCM no equivalen a los precios de ejecución de Vantage.",
    }
