"""Offline, research-only XAUUSD liquidity primitives for Gedeon 2.0.

No network, broker, scanner, notifications, or order execution.
Input is the user's gzip JSON M1 export with Bid (o/h/l/c) and Ask (ao/ah/al/ac).
"""
from __future__ import annotations
import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Bar:
    ts: int
    o: float
    h: float
    l: float
    c: float
    ao: float
    ah: float
    al: float
    ac: float


def load_m1(path: str | Path) -> list[Bar]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        raw = json.load(f)
    bars = [
        Bar(int(x["ts"]), *(float(x[k]) for k in ("o", "h", "l", "c", "ao", "ah", "al", "ac")))
        for x in raw
    ]
    bars.sort(key=lambda b: b.ts)
    if len({b.ts for b in bars}) != len(bars):
        raise ValueError("duplicate timestamps")
    for b in bars:
        if not (b.l <= min(b.o, b.c) <= max(b.o, b.c) <= b.h):
            raise ValueError(f"invalid Bid OHLC at {b.ts}")
        if not (b.al <= min(b.ao, b.ac) <= max(b.ao, b.ac) <= b.ah):
            raise ValueError(f"invalid Ask OHLC at {b.ts}")
    return bars


def aggregate(bars: Iterable[Bar], minutes: int) -> list[Bar]:
    """Aggregate only complete, epoch-aligned M1 buckets; never fill missing bars."""
    if minutes not in (5, 15, 30):
        raise ValueError("minutes must be 5, 15 or 30")
    src = list(bars)
    out = []
    bucket = []
    key = None
    for b in src:
        k = b.ts // (minutes * 60)
        if key is not None and k != key:
            if (
                len(bucket) == minutes
                and bucket[0].ts == key * minutes * 60
                and all(bucket[i].ts - bucket[i - 1].ts == 60 for i in range(1, minutes))
            ):
                out.append(_combine(bucket))
            bucket = []
        key = k
        bucket.append(b)
    if (
        len(bucket) == minutes
        and bucket[0].ts == key * minutes * 60
        and all(bucket[i].ts - bucket[i - 1].ts == 60 for i in range(1, minutes))
    ):
        out.append(_combine(bucket))
    return out


def _combine(xs: list[Bar]) -> Bar:
    a, z = xs[0], xs[-1]
    return Bar(
        a.ts, a.o, max(x.h for x in xs), min(x.l for x in xs), z.c,
        a.ao, max(x.ah for x in xs), min(x.al for x in xs), z.ac,
    )


def ema(values: list[float], period: int) -> list[float | None]:
    if period < 1:
        raise ValueError("period must be positive")
    out = [None] * len(values)
    if len(values) < period:
        return out
    value = sum(values[:period]) / period
    out[period - 1] = value
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        value = values[i] * alpha + value * (1 - alpha)
        out[i] = value
    return out


def confirmed_pivots(bars: list[Bar], left: int = 2, right: int = 2):
    """Return pivot highs/lows only at their confirmation index (no look-ahead)."""
    highs = {}
    lows = {}
    for i in range(left + right, len(bars)):
        p = i - right
        window = bars[p - left:p + right + 1]
        if bars[p].h == max(x.h for x in window) and sum(x.h == bars[p].h for x in window) == 1:
            highs[i] = bars[p].h
        if bars[p].l == min(x.l for x in window) and sum(x.l == bars[p].l for x in window) == 1:
            lows[i] = bars[p].l
    return highs, lows


def liquidity_sweeps(bars: list[Bar], levels: list[float]):
    """A sweep is wick-through then completed close back inside the level."""
    events = []
    for i, b in enumerate(bars):
        for level in levels:
            if b.h > level and b.c < level:
                events.append((i, "bearish_sweep", level))
            if b.l < level and b.c > level:
                events.append((i, "bullish_sweep", level))
    return events


def audit(bars: list[Bar]) -> dict:
    gaps = {}
    for a, b in zip(bars, bars[1:]):
        delta = b.ts - a.ts
        if delta > 60:
            gaps[delta] = gaps.get(delta, 0) + 1
    spreads = sorted(b.ao - b.o for b in bars)

    def quantile(p):
        return spreads[min(len(spreads) - 1, int((len(spreads) - 1) * p))]

    return {
        "m1_bars": len(bars),
        "first_utc": datetime.fromtimestamp(bars[0].ts, timezone.utc).isoformat(),
        "last_utc": datetime.fromtimestamp(bars[-1].ts, timezone.utc).isoformat(),
        "duplicates": 0,
        "gap_count": sum(gaps.values()),
        "gap_steps_seconds": dict(sorted(gaps.items())),
        "spread_median": quantile(0.5),
        "spread_p90": quantile(0.9),
        "spread_p99": quantile(0.99),
        "spread_max": spreads[-1],
    }
