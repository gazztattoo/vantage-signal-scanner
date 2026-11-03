"""Normalize SiftingIO technical-signal responses for research-only consensus.

No network calls, credentials, scheduling, persistence, or trading execution.
The caller must obtain responses through an authorized SiftingIO account/API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SOURCE = "siftingio"
SUPPORTED = {
    "XAU/USD": ("commodities", "XAUUSD"),
    "EUR/USD": ("forex", "EURUSD"),
    "BTC/USD": ("crypto", "BTCUSD"),
}
INTERVALS = {"5m", "15m", "30m"}


def normalize_sifting_signal(payload: Any, *, symbol: str, interval: str,
                             now: datetime | None = None,
                             max_age_minutes: int = 20) -> dict:
    """Convert one documented SiftingIO live-signal response into a safe record.

    Forming candles are rejected by default: consensus only uses closed bars.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return {"accepted": False, "reason": "La hora de referencia debe incluir zona horaria."}
    now = now.astimezone(timezone.utc)
    if symbol not in SUPPORTED or interval not in INTERVALS:
        return {"accepted": False, "reason": "Activo o temporalidad no admitidos."}
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return {"accepted": False, "reason": "Respuesta del proveedor inválida."}

    data = payload["data"]
    meta = payload.get("meta") or {}
    expected_venue, expected_symbol = SUPPORTED[symbol]
    if meta.get("symbol") != expected_symbol or meta.get("interval") != interval:
        return {"accepted": False, "reason": "La respuesta no corresponde al activo o temporalidad solicitados."}
    price = data.get("price") or {}
    if price.get("bar_status") != "closed":
        return {"accepted": False, "reason": "La vela del proveedor sigue formándose."}

    as_of_raw = meta.get("as_of")
    if not isinstance(as_of_raw, str):
        return {"accepted": False, "reason": "Falta la fecha de cálculo del proveedor."}
    try:
        as_of = datetime.fromisoformat(as_of_raw.replace("Z", "+00:00"))
        if as_of.tzinfo is None:
            raise ValueError()
        as_of = as_of.astimezone(timezone.utc)
    except ValueError:
        return {"accepted": False, "reason": "Fecha del proveedor inválida."}
    age = (now - as_of).total_seconds() / 60
    if age < -1 or age > max_age_minutes:
        return {"accepted": False, "reason": "Señal externa fuera de la ventana de frescura.",
                "age_minutes": round(age, 1)}

    summary = data.get("summary") or {}
    direction = summary.get("signal")
    score = summary.get("score")
    if direction not in {"strong_sell", "sell", "neutral", "buy", "strong_buy"}:
        return {"accepted": False, "reason": "Dirección externa inválida."}
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not -1 <= score <= 1:
        return {"accepted": False, "reason": "Puntuación externa inválida."}

    close = price.get("close")
    if isinstance(close, bool) or not isinstance(close, (int, float)) or close <= 0:
        return {"accepted": False, "reason": "Precio de referencia inválido."}

    return {
        "accepted": True,
        "source": SOURCE,
        "venue": expected_venue,
        "symbol": symbol,
        "interval": interval,
        "direction": direction.upper(),
        "score": float(score),
        "close_reference": float(close),
        "as_of": as_of.isoformat(),
        "age_minutes": round(age, 1),
        "bar_status": "closed",
        "oscillators": data.get("oscillators") or {},
        "moving_averages": data.get("moving_averages") or {},
        "role": "EXTERNAL_TECHNICAL_CONTEXT_ONLY",
        "warning": "Señal técnica externa; no es una entrada ni una probabilidad de éxito.",
    }
