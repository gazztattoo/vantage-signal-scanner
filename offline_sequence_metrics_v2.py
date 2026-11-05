"""Outcome metrics for offline sequence simulations.

These metrics summarize supplied outcomes only. They are not a market backtest,
do not estimate future profitability, and exclude unresolved trades from R expectancy.
"""
from collections import Counter


VALID_OUTCOMES = {"TP", "SL", "SL_AMBIGUOUS", "UNRESOLVED"}


def summarize_outcomes(outcomes, *, reward_risk=2.0):
    """Return transparent counts, resolved hit rate, and conservative R expectancy."""
    try:
        rr = float(reward_risk)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("reward_risk must be a finite positive number")
    if not __import__("math").isfinite(rr) or rr <= 0:
        raise ValueError("reward_risk must be a finite positive number")

    values = list(outcomes)
    unknown = [value for value in values if value not in VALID_OUTCOMES]
    if unknown:
        raise ValueError(f"unknown outcomes: {unknown}")

    counts = Counter(values)
    wins = counts["TP"]
    losses = counts["SL"] + counts["SL_AMBIGUOUS"]
    resolved = wins + losses
    total = len(values)
    net_r = wins * rr - losses
    return {
        "total": total,
        "tp": wins,
        "sl": counts["SL"],
        "sl_ambiguous": counts["SL_AMBIGUOUS"],
        "unresolved": counts["UNRESOLVED"],
        "resolved": resolved,
        "win_rate_percent": round(100 * wins / resolved, 2) if resolved else None,
        "net_r": round(net_r, 4),
        "expectancy_r_per_resolved_trade": round(net_r / resolved, 4) if resolved else None,
        "unresolved_excluded_from_expectancy": True,
        "ambiguous_counted_as_loss": True,
        "note": "Offline scenario metric only; not evidence of live-market performance.",
    }
