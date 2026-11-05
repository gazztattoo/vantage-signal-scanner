"""Anticipatory preparation gate for Global Signal Scanner V2.

Pure research logic only: no market-data access, persistence, notifications,
credentials, broker integration, or order execution.
"""
from __future__ import annotations

from math import isfinite


VALID_ORDERS = {
    ("BUY", "BREAKOUT"): "BUY_STOP",
    ("SELL", "BREAKOUT"): "SELL_STOP",
    ("BUY", "PULLBACK"): "BUY_LIMIT",
    ("SELL", "PULLBACK"): "SELL_LIMIT",
}


def _number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate_preparation(*, symbol, side, setup_type, current_price, entry,
                         stop_loss, take_profit, atr, approach_speed_per_second,
                         spread, min_lead_seconds=180, max_lead_seconds=300,
                         max_spread_atr_fraction=0.10, min_reward_risk=2.0):
    """Return PREPARE, TOO_LATE, or WAIT from a proposed pending-order setup.

    approach_speed_per_second is the recent absolute price velocity toward
    the planned entry, measured over completed observations. It is an estimate,
    not a promise that the market will arrive within that time.
    """
    order_type = VALID_ORDERS.get((side, setup_type))
    if not symbol or not order_type:
        return {"status": "WAIT", "reason": "Activo, dirección o tipo de setup inválido."}

    names = ("current_price", "entry", "stop_loss", "take_profit", "atr",
             "approach_speed_per_second", "spread")
    values = {name: _number(locals()[name]) for name in names}
    if any(value is None for value in values.values()):
        return {"status": "WAIT", "reason": "Hay precios o métricas ausentes/no finitos."}
    current = values["current_price"]
    entry_price = values["entry"]
    stop = values["stop_loss"]
    target = values["take_profit"]
    volatility = values["atr"]
    speed = values["approach_speed_per_second"]
    spread_value = values["spread"]
    if volatility <= 0 or speed <= 0 or spread_value < 0:
        return {"status": "WAIT", "reason": "ATR, velocidad o spread inválidos."}

    if side == "BUY":
        levels_ok = stop < entry_price < target
    else:
        levels_ok = target < entry_price < stop
    if not levels_ok:
        return {"status": "WAIT", "reason": "SL, entrada y TP no corresponden a la dirección."}

    if order_type in ("BUY_STOP", "SELL_LIMIT") and entry_price <= current:
        return {"status": "WAIT", "reason": "La entrada debe estar por encima del precio actual."}
    if order_type in ("SELL_STOP", "BUY_LIMIT") and entry_price >= current:
        return {"status": "WAIT", "reason": "La entrada debe estar por debajo del precio actual."}

    risk = abs(entry_price - stop)
    reward = abs(target - entry_price)
    rr = reward / risk if risk > 0 else 0.0
    if rr < min_reward_risk:
        return {"status": "WAIT", "reason": "Relación riesgo/beneficio insuficiente.",
                "risk_reward": round(rr, 2)}
    if spread_value / volatility > max_spread_atr_fraction:
        return {"status": "WAIT", "reason": "Spread demasiado alto respecto al ATR.",
                "spread_atr": round(spread_value / volatility, 4)}

    distance = abs(entry_price - current)
    lead_seconds = distance / speed
    result = {
        "symbol": symbol, "side": side, "setup_type": setup_type,
        "order_type": order_type, "entry": entry_price,
        "stop_loss": stop, "take_profit": target,
        "estimated_lead_seconds": round(lead_seconds),
        "risk_reward": round(rr, 2),
        "status": "PREPARE",
        "notification": "PREPARAR MT5",
        "warning": "Anticipación estimada; no garantiza el tiempo de llegada ni la ejecución.",
    }
    if lead_seconds < min_lead_seconds:
        result.update(status="TOO_LATE", notification=None,
                      reason="El precio podría llegar antes de que puedas preparar MT5.")
    elif lead_seconds > max_lead_seconds:
        result.update(status="WAIT", notification=None,
                      reason="La entrada aún está demasiado lejos; esperar una nueva evaluación.")
    return result


def validate_entry(*, preparation, current_price, spread, atr,
                   trigger_confirmed, structure_valid,
                   max_entry_deviation_atr=0.10,
                   max_spread_atr_fraction=0.10):
    """Validate phase two against a fresh quote and explicit closed-bar trigger."""
    if not isinstance(preparation, dict) or preparation.get("status") != "PREPARE":
        return {"status": "WAIT", "notification": None,
                "reason": "No existe una preparación vigente."}
    current = _number(current_price)
    spread_value = _number(spread)
    volatility = _number(atr)
    entry = _number(preparation.get("entry"))
    if None in (current, spread_value, volatility, entry) or volatility <= 0 or spread_value < 0:
        return {"status": "WAIT", "notification": None, "reason": "Cotización o métricas inválidas."}
    if not structure_valid:
        return {"status": "INVALIDATED", "notification": None,
                "reason": "La estructura que justificaba la preparación dejó de ser válida."}
    if spread_value / volatility > max_spread_atr_fraction:
        return {"status": "WAIT", "notification": None, "reason": "Spread fuera del límite permitido."}
    if not trigger_confirmed:
        return {"status": "WAIT", "notification": None, "reason": "Falta confirmación de activación."}
    if abs(current - entry) > volatility * max_entry_deviation_atr:
        return {"status": "TOO_LATE", "notification": None,
                "reason": "El precio se alejó de la entrada prevista; no perseguir el movimiento."}
    return {
        "status": "VALIDATED", "notification": "ENTRADA VALIDADA",
        "symbol": preparation.get("symbol"), "side": preparation.get("side"),
        "order_type": preparation.get("order_type"), "entry": entry,
        "stop_loss": preparation.get("stop_loss"),
        "take_profit": preparation.get("take_profit"),
        "warning": "Validación técnica; comprobar Bid/Ask, spread y condiciones de Vantage antes de operar manualmente.",
    }
