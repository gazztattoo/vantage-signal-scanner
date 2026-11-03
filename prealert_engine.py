"""Pure, provider-free state machine for VANTAGE early-entry alerts.

This module deliberately does not fetch market data, place orders, send notifications,
or import the Flask application. Callers must supply a timestamped local snapshot.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


ACTIVE_STATES = {"PREALERTA"}
TERMINAL_STATES = {"CONFIRMADA", "CANCELADA", "EXPIRADA"}


def _result(state: str, previous: Optional[dict], now_ts: float,
            reason: str, snapshot: dict) -> dict:
    created_at = (previous or {}).get("created_at", now_ts)
    return {
        "state": state,
        "instrument": snapshot.get("instrument"),
        "timeframe": snapshot.get("timeframe"),
        "direction": snapshot.get("direction"),
        "zone_low": snapshot.get("zone_low"),
        "zone_high": snapshot.get("zone_high"),
        "created_at": created_at,
        "updated_at": now_ts,
        "reason": reason,
        "event": state if not previous or previous.get("state") != state else None,
    }


def evaluate_prealert(
    snapshot: dict,
    previous: Optional[dict] = None,
    now_ts: Optional[float] = None,
) -> dict:
    """Evaluate one local snapshot and return the next state without side effects.

    Required snapshot fields:
      instrument, timeframe, timestamp, price, direction, zone_low, zone_high,
      trend_aligned, momentum_aligned, structure_valid, approach_distance.

    A PREALERTA is an early warning when price approaches (but has not entered)
    the zone from the expected side. CONFIRMADA means price has entered the zone
    while all supplied setup conditions remain valid; it is not an order signal.
    """
    if now_ts is None:
        raise ValueError("now_ts is required for deterministic evaluation")

    required = {
        "instrument", "timeframe", "timestamp", "price", "direction",
        "zone_low", "zone_high", "trend_aligned", "momentum_aligned",
        "structure_valid", "approach_distance",
    }
    missing = required.difference(snapshot)
    if missing:
        raise ValueError(f"missing snapshot fields: {', '.join(sorted(missing))}")

    direction = snapshot["direction"]
    if direction not in {"BUY", "SELL"}:
        raise ValueError("direction must be BUY or SELL")

    price = float(snapshot["price"])
    low = float(snapshot["zone_low"])
    high = float(snapshot["zone_high"])
    stamp = float(snapshot["timestamp"])
    approach = float(snapshot["approach_distance"])
    if low > high or approach <= 0:
        raise ValueError("invalid zone or approach_distance")

    previous = previous or None
    if previous and previous.get("state") in TERMINAL_STATES:
        return _result(previous["state"], previous, now_ts,
                       previous.get("reason", "terminal state"), snapshot)

    max_age = float(snapshot.get("max_data_age_seconds", 90))
    age = float(now_ts) - stamp
    if age < 0 or age > max_age:
        return _result("CANCELADA", previous, now_ts,
                       "datos ausentes, futuros o desactualizados", snapshot)

    valid = bool(snapshot["trend_aligned"] and
                 snapshot["momentum_aligned"] and
                 snapshot["structure_valid"])
    if not valid:
        return _result("CANCELADA", previous, now_ts,
                       "la confluencia o la estructura dejó de ser válida",
                       snapshot)

    inside = low <= price <= high
    if inside:
        return _result("CONFIRMADA", previous, now_ts,
                       "precio dentro de la zona y condiciones vigentes",
                       snapshot)

    # BUY setups approach the zone from above; SELL setups from below.
    correct_side = price > high if direction == "BUY" else price < low
    if not correct_side:
        return _result("CANCELADA", previous, now_ts,
                       "el precio atravesó la zona sin confirmación", snapshot)

    distance = price - high if direction == "BUY" else low - price
    if previous and previous.get("state") == "PREALERTA":
        ttl = float(snapshot.get("prealert_ttl_seconds", 180))
        if float(now_ts) - float(previous.get("created_at", now_ts)) > ttl:
            return _result("EXPIRADA", previous, now_ts,
                           "venció el tiempo de la prealerta", snapshot)

    if distance <= approach:
        return _result("PREALERTA", previous, now_ts,
                       f"precio aproximándose a la zona; distancia={distance:.6g}",
                       snapshot)

    if previous and previous.get("state") == "PREALERTA":
        return _result("CANCELADA", previous, now_ts,
                       "el precio se alejó de la zona de entrada", snapshot)

    return _result("SIN_ALERTA", previous, now_ts,
                   "todavía fuera del rango de anticipación", snapshot)
