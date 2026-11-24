"""M15-led alert lifecycle helpers for the TEST scanner.

All prices are reference-only until independently checked in Vantage MT5.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

_PREALERT_PREFIX = "M15_PREALERT|"


def _stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _age_minutes(value: Any, now: datetime) -> float | None:
    stamp = _stamp(value)
    if stamp is None:
        return None
    return (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds() / 60.0


def build_m15_prealert_context(data: dict[str, Any]) -> dict[str, Any] | None:
    if data.get("pre_signal") is not True or data.get("pre_signal_side") not in ("BUY", "SELL"):
        return None
    try:
        entry = float(data["planned_entry"])
        stop = float(data["planned_stop_loss"])
        target = float(data["planned_take_profit"])
        atr = float(data["atr14"])
    except (KeyError, TypeError, ValueError):
        return None
    if min(entry, stop, target, atr) <= 0 or not data.get("candle_time"):
        return None
    if data["pre_signal_side"] == "BUY" and not (stop < entry < target):
        return None
    if data["pre_signal_side"] == "SELL" and not (stop > entry > target):
        return None
    age = _age_minutes(data.get("candle_time"), datetime.now(timezone.utc))
    if age is None or age < -2 or age > 20:
        return None
    return {
        "side": data["pre_signal_side"],
        "entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "atr": atr,
        "candle_time": str(data["candle_time"]),
    }


def prealert_entry_not_crossed(context: dict[str, Any], quote: dict[str, Any]) -> bool:
    """A prealert is useful only while the planned trigger is still ahead of price."""
    if not context or quote.get("accepted") is not True:
        return False
    try:
        age = float(quote["quote_age_seconds"])
        bid, ask = float(quote["bid"]), float(quote["ask"])
        entry = float(context["entry"])
        side = context["side"]
    except (KeyError, TypeError, ValueError):
        return False
    if age < 0 or age > 60 or bid <= 0 or ask < bid:
        return False
    current = ask if side == "BUY" else bid
    return current < entry if side == "BUY" else current > entry


def encode_m15_prealert_context(context: dict[str, Any]) -> str:
    return _PREALERT_PREFIX + json.dumps(context, separators=(",", ":"), sort_keys=True)


def restore_m15_prealert_context(
    fingerprint: Any,
    direction: Any,
    now: datetime | None = None,
    max_age_minutes: float = 20.0,
) -> dict[str, Any] | None:
    if not isinstance(direction, str) or not direction.startswith("PREPARAR_"):
        return None
    if not isinstance(fingerprint, str) or not fingerprint.startswith(_PREALERT_PREFIX):
        return None
    try:
        context = json.loads(fingerprint[len(_PREALERT_PREFIX):])
    except (TypeError, ValueError):
        return None
    if not isinstance(context, dict) or context.get("side") not in ("BUY", "SELL"):
        return None
    try:
        for key in ("entry", "stop_loss", "take_profit", "atr"):
            context[key] = float(context[key])
        if min(context["entry"], context["stop_loss"], context["take_profit"], context["atr"]) <= 0:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    age = _age_minutes(context.get("candle_time"), now)
    if age is None or age < -2 or age > max_age_minutes:
        return None
    expected = "PREPARAR_COMPRA" if context["side"] == "BUY" else "PREPARAR_VENTA"
    if direction != expected:
        return None
    context["age_minutes"] = age
    return context


def evaluate_m15_confirmation(
    prealert: dict[str, Any] | None,
    m5: dict[str, Any],
    m30: dict[str, Any] | None,
    quote: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate M5/M30 against the immutable M15 plan; never reprice the entry."""
    def reject(reason: str) -> dict[str, Any]:
        return {"valid": False, "reason": reason}

    if not prealert:
        return reject("No existe una prealerta M15 activa.")
    now = now or datetime.now(timezone.utc)
    age = _age_minutes(prealert.get("candle_time"), now)
    if age is None or age < -2 or age > 20:
        return reject("La prealerta M15 caducó.")
    side = prealert.get("side")
    expected_signal = "COMPRA" if side == "BUY" else "VENTA"
    if m5.get("signal") != expected_signal:
        return reject("M5 todavía no confirma la misma dirección.")
    if m5.get("structure_trigger") in (None, "", "NONE", "PENDING"):
        return reject("M5 todavía no confirma la ruptura o el retest.")
    try:
        m5_age = float(m5["candle_age_minutes"])
    except (KeyError, TypeError, ValueError):
        return reject("No se pudo verificar la frescura de M5.")
    if m5_age < -2 or m5_age > 7:
        return reject("La confirmación M5 está desactualizada.")

    if not isinstance(m30, dict):
        return reject("Falta el contexto interno M30.")
    bias = m30.get("primary_bias")
    allowed_bias = ("BULL", "NEUTRAL") if side == "BUY" else ("BEAR", "NEUTRAL")
    if bias not in allowed_bias:
        return reject("El contexto M30 contradice la dirección de M15.")
    try:
        m30_age = float(m30["candle_age_minutes"])
    except (KeyError, TypeError, ValueError):
        return reject("No se pudo verificar la frescura de M30.")
    if m30_age < -2 or m30_age > 35:
        return reject("El contexto M30 está desactualizado.")

    if quote.get("accepted") is not True:
        return reject("No hay una cotización de referencia válida.")
    try:
        quote_age = float(quote["quote_age_seconds"])
        bid, ask = float(quote["bid"]), float(quote["ask"])
        entry, atr_value = float(prealert["entry"]), float(prealert["atr"])
    except (KeyError, TypeError, ValueError):
        return reject("La cotización o el nivel M15 no son válidos.")
    if quote_age < 0 or quote_age > 60 or bid <= 0 or ask < bid or atr_value <= 0:
        return reject("La cotización de referencia está antigua o es inválida.")

    current = ask if side == "BUY" else bid
    crossed = current >= entry if side == "BUY" else current <= entry
    if not crossed:
        return reject("El precio todavía no ha activado el nivel previsto.")
    drift = abs(current - entry)
    # Hard cap prevents a wide ATR from allowing a late chase of the market.
    max_drift = min(0.50, max(0.05, 0.10 * atr_value))
    if drift > max_drift:
        return reject("El precio ya se alejó demasiado de la entrada M15; oportunidad descartada.")

    return {
        "valid": True,
        "reason": "M5 confirma, M30 no contradice y el precio sigue junto al nivel M15.",
        "entry": entry,
        "stop_loss": float(prealert["stop_loss"]),
        "take_profit": float(prealert["take_profit"]),
        "reference_price": current,
        "drift": drift,
        "max_drift": max_drift,
        "opportunity_candle_time": prealert["candle_time"],
    }
