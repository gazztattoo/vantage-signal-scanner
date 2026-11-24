"""Causal liquidity diagnostics for Gedeón 2.0.

This module is research/diagnostic only. It does not emit trade entries.
Pivots are only made available after the right-hand confirmation bars close;
all sweeps and structure breaks are evaluated using information available at
that candle, avoiding look-ahead bias.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


def _price_bars(candles: list[dict]) -> list[dict]:
    bars = []
    previous = None
    for raw in candles:
        stamp = raw.get("time", raw.get("timestamp"))
        bar = {"time": stamp}
        for key in ("open", "high", "low", "close"):
            value = float(raw[key])
            if not isfinite(value):
                raise ValueError(f"Invalid {key}")
            bar[key] = value
        if bar["high"] < max(bar["open"], bar["low"], bar["close"]):
            raise ValueError("Invalid candle high")
        if bar["low"] > min(bar["open"], bar["high"], bar["close"]):
            raise ValueError("Invalid candle low")
        if previous is not None and stamp is not None and previous is not None and str(stamp) <= str(previous):
            raise ValueError("Candles must be chronological")
        previous = stamp
        bars.append(bar)
    return bars


def _atr(bars: list[dict], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    tr = []
    for i in range(1, len(bars)):
        cur, prev = bars[i], bars[i - 1]
        tr.append(max(cur["high"] - cur["low"],
                      abs(cur["high"] - prev["close"]),
                      abs(cur["low"] - prev["close"])))
    return sum(tr[-period:]) / period


def analyze_liquidity(
    candles: list[dict],
    atr_value: float | None = None,
    left_bars: int = 3,
    right_bars: int = 3,
    lookback: int = 160,
    equal_tolerance_atr: float = 0.12,
) -> dict:
    """Return confirmed swing pools, sweeps, BOS/CHoCH and a candidate OB.

    A swing at j is only confirmed at j + right_bars. On candle i, only
    pivots with j + right_bars < i are eligible to be swept/broken.
    """
    bars = _price_bars(candles)
    empty = {
        "available": False, "bias": "NEUTRAL", "zones": [],
        "last_sweep": None, "last_structure_event": None,
        "order_block": None, "explanation": "Historial insuficiente para estructura de liquidez.",
        "lookahead_safe": True,
    }
    minimum = max(left_bars + right_bars + 5, 2 * left_bars + 10)
    if len(bars) < minimum:
        return empty

    atr_now = float(atr_value) if atr_value is not None else (_atr(bars) or 0.0)
    if not isfinite(atr_now) or atr_now <= 0:
        return empty
    tol = max(atr_now * equal_tolerance_atr, 1e-8)
    start = max(0, len(bars) - lookback)
    pivots_high: list[dict] = []
    pivots_low: list[dict] = []
    sweeps: list[dict] = []
    structure_events: list[dict] = []
    trend = "NEUTRAL"

    for i in range(start, len(bars)):
        # Confirm only pivots whose complete right-hand window is already closed.
        candidate_j = i - right_bars
        if candidate_j >= left_bars:
            window = bars[candidate_j-left_bars:candidate_j+right_bars+1]
            center = bars[candidate_j]
            if center["high"] == max(x["high"] for x in window) and sum(
                1 for x in window if x["high"] == center["high"]
            ) == 1:
                pivots_high.append({"index": candidate_j, "confirmed_at": i,
                                    "price": center["high"], "time": center["time"]})
            if center["low"] == min(x["low"] for x in window) and sum(
                1 for x in window if x["low"] == center["low"]
            ) == 1:
                pivots_low.append({"index": candidate_j, "confirmed_at": i,
                                   "price": center["low"], "time": center["time"]})

        # Exclude a pivot on its confirmation candle: it can only be swept later.
        eligible_highs = [p for p in pivots_high if p["confirmed_at"] < i and i-p["index"] <= lookback]
        eligible_lows = [p for p in pivots_low if p["confirmed_at"] < i and i-p["index"] <= lookback]
        if not eligible_highs or not eligible_lows:
            continue
        bar = bars[i]
        prior_high = max(eligible_highs, key=lambda p: p["index"])
        prior_low = max(eligible_lows, key=lambda p: p["index"])

        # Sweep: wick takes a confirmed pool, but the candle closes back through it.
        swept_high = [p for p in eligible_highs if bar["high"] > p["price"] + tol and bar["close"] < p["price"]]
        swept_low = [p for p in eligible_lows if bar["low"] < p["price"] - tol and bar["close"] > p["price"]]
        if swept_high:
            pool = max(swept_high, key=lambda p: p["index"])
            sweeps.append({"side": "BEARISH", "pool": "BUY_SIDE_LIQUIDITY",
                           "level": pool["price"], "sweep_high": bar["high"],
                           "close": bar["close"], "candle_time": bar["time"],
                           "pivot_time": pool["time"], "index": i})
        if swept_low:
            pool = max(swept_low, key=lambda p: p["index"])
            sweeps.append({"side": "BULLISH", "pool": "SELL_SIDE_LIQUIDITY",
                           "level": pool["price"], "sweep_low": bar["low"],
                           "close": bar["close"], "candle_time": bar["time"],
                           "pivot_time": pool["time"], "index": i})

        # Structure break on close through the latest previously confirmed swing.
        broken = None
        if bar["close"] > prior_high["price"] + tol:
            direction = "BULLISH"
            kind = "CHoCH" if trend == "BEARISH" else "BOS"
            broken = {"direction": direction, "type": kind, "level": prior_high["price"],
                      "candle_time": bar["time"], "index": i}
        elif bar["close"] < prior_low["price"] - tol:
            direction = "BEARISH"
            kind = "CHoCH" if trend == "BULLISH" else "BOS"
            broken = {"direction": direction, "type": kind, "level": prior_low["price"],
                      "candle_time": bar["time"], "index": i}
        if broken:
            # Candidate OB = last opposite-colour candle before the displacement break.
            opposite = "BEARISH" if direction == "BULLISH" else "BULLISH"
            ob = None
            # Require a meaningful displacement candle before calling the prior
            # opposite candle an order-block candidate.
            displacement = abs(bar["close"] - bar["open"]) >= 0.8 * atr_now
            if displacement:
                for k in range(i-1, max(start, i-11)-1, -1):
                    prior = bars[k]
                    candle_side = "BULLISH" if prior["close"] > prior["open"] else (
                        "BEARISH" if prior["close"] < prior["open"] else "DOJI"
                    )
                    if candle_side == opposite:
                        ob = {"direction": direction, "source": "last_opposite_candle_before_displacement",
                              "high": prior["high"], "low": prior["low"],
                              "candle_time": prior["time"], "index": k}
                        break
            broken["order_block_candidate"] = ob
            structure_events.append(broken)
            trend = direction

    # Latest active pivot pools and equal-high/low clusters are diagnostic zones.
    recent_highs = [p for p in pivots_high if len(bars)-1-p["index"] <= lookback]
    recent_lows = [p for p in pivots_low if len(bars)-1-p["index"] <= lookback]

    def zone_groups(points: list[dict], kind: str) -> list[dict]:
        groups: list[list[dict]] = []
        for point in sorted(points, key=lambda p: p["price"]):
            match = next((g for g in groups if abs(point["price"] -
                         sum(x["price"] for x in g)/len(g)) <= tol), None)
            if match is None:
                groups.append([point])
            else:
                match.append(point)
        zones = []
        for group in groups:
            if len(group) < 2:
                continue
            level = sum(x["price"] for x in group)/len(group)
            outer_edge = (max if kind == "high" else min)(p["price"] for p in points)
            zones.append({"type": "EQUAL_HIGHS" if kind == "high" else "EQUAL_LOWS",
                          "pool": "BUY_SIDE_LIQUIDITY" if kind == "high" else "SELL_SIDE_LIQUIDITY",
                          "level": round(level, 5), "touches": len(group),
                          "first_time": group[0]["time"], "last_time": group[-1]["time"],
                          "classification": "EXTERNAL" if abs(level-outer_edge) <= 2*tol
                                            else "INTERNAL"})
        return zones

    zones = zone_groups(recent_highs, "high") + zone_groups(recent_lows, "low")
    for pool, points, kind in (
        ("BUY_SIDE_LIQUIDITY", recent_highs, "SWING_HIGH"),
        ("SELL_SIDE_LIQUIDITY", recent_lows, "SWING_LOW"),
    ):
        if points:
            extreme = (max if kind == "SWING_HIGH" else min)(p["price"] for p in points)
            latest = max(points, key=lambda p: p["index"])
            zones.append({"type": kind, "pool": pool, "level": round(extreme, 5),
                          "touches": 1, "last_time": latest["time"],
                          "classification": "EXTERNAL"})
            inner = max(points, key=lambda p: p["index"])
            if abs(inner["price"]-extreme) > tol:
                zones.append({"type": kind, "pool": pool, "level": round(inner["price"], 5),
                              "touches": 1, "last_time": inner["time"],
                              "classification": "INTERNAL"})

    last_sweep = sweeps[-1] if sweeps else None
    last_event = structure_events[-1] if structure_events else None
    ob = last_event.get("order_block_candidate") if last_event else None
    bias = last_sweep["side"] if last_sweep else (
        last_event["direction"] if last_event else trend
    )
    return {
        "available": True, "bias": bias, "zones": zones[-12:],
        "last_sweep": last_sweep, "last_structure_event": last_event,
        "order_block": ob,
        "confirmed_pivots": {"highs": len(recent_highs), "lows": len(recent_lows)},
        "equal_high_low_zones": sum(1 for z in zones if z["type"].startswith("EQUAL_")),
        "atr_tolerance": round(tol, 6), "lookahead_safe": True,
        "explanation": "Zonas y eventos calculados solo con pivotes confirmados; diagnóstico, no señal de entrada.",
    }
