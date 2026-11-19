"""Offline integrity audit for XAU/USD OHLC CSV files.

No network, provider API, scanner, notifications, or order execution.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path


ALIASES = {
    "timestamp": ("timestamp", "time", "datetime", "date", "ts"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c"),
}


def _parse_time(value: str) -> datetime:
    raw = str(value).strip()
    if raw.isdigit():
        number = int(raw)
        # Unix seconds; reject milliseconds rather than silently misread.
        if number > 10_000_000_000:
            number //= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone or be Unix epoch")
    return parsed.astimezone(timezone.utc)


def _column(headers, key):
    normalized = {str(h).strip().lower(): h for h in headers}
    for alias in ALIASES[key]:
        if alias in normalized:
            return normalized[alias]
    raise ValueError(f"missing required column: {key}")


def audit_csv(path: str | Path, timeframe_minutes: int) -> dict:
    if timeframe_minutes not in (1, 5, 15, 30):
        raise ValueError("timeframe_minutes must be 1, 5, 15 or 30")
    rows = []
    invalid = 0
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        cols = {key: _column(reader.fieldnames, key) for key in ALIASES}
        for row in reader:
            try:
                stamp = _parse_time(row[cols["timestamp"]])
                values = {key: float(row[cols[key]]) for key in ("open", "high", "low", "close")}
                if not all(math.isfinite(v) and v > 0 for v in values.values()):
                    raise ValueError("non-finite or non-positive OHLC")
                if values["high"] < max(values.values()) or values["low"] > min(values.values()):
                    raise ValueError("invalid OHLC envelope")
                rows.append((stamp, values))
            except (ValueError, TypeError, KeyError, OverflowError):
                invalid += 1

    rows.sort(key=lambda item: item[0])
    unique = []
    duplicate_timestamps = 0
    for row in rows:
        if unique and row[0] == unique[-1][0]:
            duplicate_timestamps += 1
            unique[-1] = row
        else:
            unique.append(row)

    step = timeframe_minutes * 60
    gaps = []
    for previous, current in zip(unique, unique[1:]):
        delta = int((current[0] - previous[0]).total_seconds())
        if delta <= 0:
            continue
        if delta > step * 1.5:
            gaps.append({"from": previous[0].isoformat(), "to": current[0].isoformat(),
                         "missing_intervals_estimate": max(round(delta / step) - 1, 1)})

    return {
        "file": Path(path).name,
        "timeframe_minutes": timeframe_minutes,
        "rows_valid": len(rows),
        "rows_unique": len(unique),
        "invalid_rows": invalid,
        "duplicate_timestamps": duplicate_timestamps,
        "gap_count": len(gaps),
        "gap_examples": gaps[:20],
        "start_utc": unique[0][0].isoformat() if unique else None,
        "end_utc": unique[-1][0].isoformat() if unique else None,
        "status": "PASS" if len(unique) >= 1000 and invalid == 0 and duplicate_timestamps == 0 else "REVIEW",
        "limitations": [
            "OHLC integrity only; this does not prove feed accuracy or tradability.",
            "Gaps are reported, not filled. Market closures may be legitimate.",
            "No spread/slippage validation is possible without bid/ask or execution-cost data.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("--timeframe", type=int, choices=(1, 5, 15, 30), required=True)
    args = parser.parse_args()
    print(json.dumps(audit_csv(args.csv_path, args.timeframe), indent=2))


if __name__ == "__main__":
    main()
