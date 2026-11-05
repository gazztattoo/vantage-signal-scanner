"""Chronological, no-lookahead replay for anticipatory signal research.

Each observation is processed in input order. A preparation can only use the
current observation; validation can only use a later observation. This is an
offline harness, not a live feed, broker adapter, or historical backtest.
"""
from __future__ import annotations

from anticipatory_signal_v2 import (
    cancel_preparation,
    evaluate_preparation,
    validate_entry,
)


def replay_chronological_sequence(observations, *, max_preparation_age_seconds=300):
    """Replay timestamped observations and report actual preparation lead time.

    Observation schema:
      {
        "timestamp": numeric seconds (monotonic),
        "setup": {evaluate_preparation kwargs},  # optional
        "validation": {current_price, spread, atr, trigger_confirmed,
                       structure_valid},        # optional
      }

    The first PREPARE wins until it validates, invalidates, expires, or becomes
    too late. Later setup proposals are ignored while a preparation is active.
    """
    if max_preparation_age_seconds <= 0:
        raise ValueError("max_preparation_age_seconds must be positive")

    timeline = []
    active = None
    prepared_at = None
    previous_timestamp = None
    stats = {
        "prepare_notices": 0,
        "validated": 0,
        "invalidated_or_cancelled": 0,
        "too_late": 0,
        "waits": 0,
    }

    for index, observation in enumerate(observations):
        if not isinstance(observation, dict) or "timestamp" not in observation:
            raise ValueError(f"observation {index} needs a timestamp")
        timestamp = float(observation["timestamp"])
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("timestamps must be strictly increasing")
        previous_timestamp = timestamp

        if active is None and isinstance(observation.get("setup"), dict):
            proposed = evaluate_preparation(**observation["setup"])
            if proposed["status"] == "PREPARE":
                active = proposed
                prepared_at = timestamp
                stats["prepare_notices"] += 1
                timeline.append({
                    "timestamp": timestamp,
                    "event": "PREPARAR MT5",
                    "estimated_lead_seconds": proposed["estimated_lead_seconds"],
                    "preparation": proposed,
                })
                continue
            if proposed["status"] == "TOO_LATE":
                stats["too_late"] += 1
                timeline.append({"timestamp": timestamp, "event": "TOO_LATE",
                                 "reason": proposed.get("reason")})
            else:
                stats["waits"] += 1
                timeline.append({"timestamp": timestamp, "event": "WAIT",
                                 "reason": proposed.get("reason")})
            continue

        if active is None:
            continue

        elapsed = timestamp - prepared_at
        if elapsed > max_preparation_age_seconds:
            cancelled = cancel_preparation(
                preparation=active, reason="Tiempo máximo de preparación superado.",
                expired=True,
            )
            timeline.append({"timestamp": timestamp, "event": "CANCELAR PREPARACIÓN",
                             "elapsed_seconds": elapsed, "result": cancelled})
            stats["invalidated_or_cancelled"] += 1
            active = None
            prepared_at = None
            continue

        validation = observation.get("validation")
        if not isinstance(validation, dict):
            continue
        result = validate_entry(preparation=active, **validation)
        status = result["status"]
        if status == "VALIDATED":
            timeline.append({"timestamp": timestamp, "event": "ENTRADA VALIDADA",
                             "elapsed_seconds": elapsed, "result": result})
            stats["validated"] += 1
            active = None
            prepared_at = None
        elif status in ("INVALIDATED", "TOO_LATE"):
            event = ("CANCELAR PREPARACIÓN" if status == "INVALIDATED"
                     else "TOO_LATE")
            timeline.append({"timestamp": timestamp, "event": event,
                             "elapsed_seconds": elapsed, "result": result})
            if status == "INVALIDATED":
                stats["invalidated_or_cancelled"] += 1
            else:
                stats["too_late"] += 1
            active = None
            prepared_at = None

    if active is not None:
        timeline.append({
            "timestamp": previous_timestamp,
            "event": "UNRESOLVED_PREPARATION",
            "elapsed_seconds": previous_timestamp - prepared_at,
            "reason": "La secuencia terminó sin validación ni invalidación.",
        })

    resolved = stats["validated"] + stats["invalidated_or_cancelled"] + stats["too_late"]
    return {
        "timeline": timeline,
        "stats": stats,
        "resolved_preparations": resolved,
        "note": (
            "Replay cronológico offline. No look-ahead entre observaciones; "
            "no representa rendimiento real ni garantiza tiempo de preparación."
        ),
    }
